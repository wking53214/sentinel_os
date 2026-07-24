"""
BISG (Bayesian Improved Surname Geocoding) estimator -- C2 dimension 4's
statistical race/ethnicity estimation method under consent_model
"opt_in_required" (default), and the decline-fallback under
"opt_out_permitted".

Reference methodology: CFPB's own published BISG implementation
(github.com/cfpb/proxy-methodology), the exact source this repo's own
proxy-screen docstring already names ("name-based (BISG-style) proxying
[is] the CFPB's own documented methodology risk" -- regulatory_checks.py).
Using their specific surname list and combination formula means dimension
4 reproduces the same method the rest of Sentinel is already scoped
around, not a generic substitute invented for this module.

Three real, live data sources, verified reachable directly (not assumed):
  1. Geocoding (address -> Census tract): geocoding.geo.census.gov,
     free, no key.
  2. Tract-level race/ethnicity distribution: the ACS 5-year API
     (api.census.gov/data/.../acs/acs5, table B03002), needs a Census
     API key (CENSUS_API_KEY env var -- see key_signup.html).
  3. Surname-conditional race/ethnicity distribution: the actual 2010
     Census surname list CFPB's own methodology uses
     (www2.census.gov/topics/genealogy/2010surnames/names.zip) --
     a static file, not an API; downloaded and cached on first real use,
     not committed to this repo (9MB+ of government data does not
     belong in application source control -- see SurnameTable).

The combination formula is CFPB's own (scripts/geo_name_merger_all_
entities_over18.do in proxy-methodology), following Elliott et al.
(2009): for each race r in RACE_CATEGORIES,
    u_r = P(r | surname) * P(this geography | r)
    P(r | surname, geography) = u_r / sum(u_all_races)
where P(this geography | r) = (population of race r in this tract) /
(national population of race r) -- NOT P(r | this geography), which is
a different, more naive quantity BISG deliberately does not use.

DOCUMENTED SIMPLIFICATIONS relative to CFPB's exact reference (flagged
here, not hidden -- consistent with this module's own INDETERMINATE
posture):
  - Tract-level geography only. CFPB's reference additionally supports
    block-group (higher precision, when rooftop-accurate geocoding is
    available) and ZIP-code (lower precision, fallback) levels, chosen
    by a precision hierarchy (combine_probs.do). This implementation
    uses tract level unconditionally -- a real, defensible middle
    ground, not the full precision-selection hierarchy.
  - "Other race" handling. CFPB's reference proportionally
    redistributes the Census "Some Other Race" category across the
    other non-Hispanic groups before combining (following Word 2008).
    This implementation combines "Some Other Race" and "Two or More
    Races" directly into mult_other without that proportional
    pre-redistribution -- simpler, and a real (if coarser) treatment of
    the same underlying ambiguity, not a fabrication.
  - Unmatched surnames (not in the ~162k-surname reference list) fall
    back to the NATIONAL population race distribution as the name-only
    prior -- standard BISG convention (no name information available;
    the population base rate is the least-wrong prior), not invented
    here.

NEVER FABRICATED: any step that cannot reach real data (no API key
configured, network unreachable, surname/geography lookup genuinely
fails) makes the WHOLE estimate INDETERMINATE rather than guessing or
silently substituting a default distribution. See BISGEstimate.
indeterminate_reason.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
import zipfile
from dataclasses import dataclass
from typing import Dict, Optional
from urllib.request import urlopen

RACE_CATEGORIES = ("white", "black", "aian", "api", "mult_other", "hispanic")

_SURNAME_LIST_URL = "https://www2.census.gov/topics/genealogy/2010surnames/names.zip"
_GEOCODER_URL = "https://geocoding.geo.census.gov/geocoder/geographies/onelineaddress"
_ACS_URL = "https://api.census.gov/data/2020/acs/acs5"

# The six ACS B03002 ("Hispanic or Latino Origin by Race") variables CFPB's
# own methodology is built on: total, four non-Hispanic race-alone
# categories, non-Hispanic "other"/multiracial, and Hispanic total.
_ACS_VARS = ("B03002_001E", "B03002_003E", "B03002_004E", "B03002_005E",
             "B03002_006E", "B03002_007E", "B03002_008E", "B03002_009E",
             "B03002_012E")


@dataclass(frozen=True)
class BISGEstimate:
    """One estimate. `distribution` is None (and `indeterminate_reason`
    set) whenever any required real data source was unreachable --
    never a fabricated fallback distribution. `precision` names the
    geography level actually used ("tract") when determinate."""

    distribution: Optional[Dict[str, float]]
    indeterminate_reason: Optional[str] = None
    precision: Optional[str] = None
    method: str = "bisg_v1_tract"

    @property
    def is_determinate(self) -> bool:
        return self.distribution is not None


class BISGEstimator:
    """The pluggable interface. A concrete implementation may reach real
    data (CensusBISGEstimator) or be a deterministic test double
    (FakeBISGEstimator) -- callers depend only on this shape."""

    def estimate(self, surname: Optional[str], address: Optional[str]) -> BISGEstimate:
        raise NotImplementedError


class SurnameTable:
    """The real 2010 Census surname list -- CFPB's own source, not a
    generic substitute. Downloaded and cached on first real use; never
    committed to this repo (9MB+ of government reference data doesn't
    belong in application source control, the same reasoning this
    codebase already applies elsewhere to not vendoring large external
    datasets).

    cache_path defaults to CENSUS_SURNAME_TABLE_PATH if set, else a
    location under the system temp dir -- deliberately outside the repo
    tree either way.
    """

    def __init__(self, cache_path: Optional[str] = None):
        # tempfile.gettempdir() (not a hand-rolled TMPDIR-or-/tmp check):
        # resolves the platform's real temp directory safely, the same
        # way the standard library's own temp-file APIs do.
        self.cache_path = cache_path or os.environ.get(
            "CENSUS_SURNAME_TABLE_PATH",
            os.path.join(tempfile.gettempdir(), "sentinel_census_surnames.csv"),
        )
        self._table: Optional[Dict[str, Dict[str, float]]] = None
        self._national: Optional[Dict[str, float]] = None

    def _ensure_downloaded(self) -> bool:
        """Returns True if a usable local copy exists (downloading it if
        necessary and reachable). False -- never an exception -- if it
        cannot be obtained; callers treat that as INDETERMINATE."""
        if os.path.exists(self.cache_path):
            return True
        try:
            # nosec B310 -- _SURNAME_LIST_URL is a fixed, hardcoded
            # module-level https:// constant, never influenced by any
            # input; bandit's scheme-confusion concern (file://, etc.)
            # does not apply to a URL that never varies.
            with urlopen(_SURNAME_LIST_URL, timeout=30) as resp:  # nosec B310
                zip_bytes = resp.read()
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                csv_bytes = zf.read("Names_2010Census.csv")
            with open(self.cache_path, "wb") as f:
                f.write(csv_bytes)
            return True
        except Exception:
            return False

    def _load(self) -> bool:
        if self._table is not None:
            return True
        if not self._ensure_downloaded():
            return False
        table: Dict[str, Dict[str, float]] = {}
        totals = {r: 0.0 for r in RACE_CATEGORIES}
        count_sum = 0.0
        with open(self.cache_path, newline="") as f:
            for row in csv.DictReader(f):
                name = row["name"].strip().upper()
                probs = self._row_probabilities(row)
                table[name] = probs
                try:
                    count = float(row["count"])
                except (ValueError, KeyError):
                    continue
                count_sum += count
                for r in RACE_CATEGORIES:
                    totals[r] += probs[r] * count
        self._table = table
        # National (population-weighted) average across every listed
        # surname -- the standard BISG fallback prior for a surname not
        # on the list at all (no name information -> population base
        # rate is the least-wrong prior; see module docstring).
        self._national = ({r: totals[r] / count_sum for r in RACE_CATEGORIES}
                          if count_sum > 0 else None)
        return True

    @staticmethod
    def _row_probabilities(row: Dict[str, str]) -> Dict[str, float]:
        """(S) marks a Census-suppressed small-sample value -- treated
        as 0.0 for that category (documented, not guessed) rather than
        imputed. mult_other reads the file's pct2prace column."""
        def pct(key: str) -> float:
            raw = row.get(key, "0").strip()
            if raw in ("", "(S)"):
                return 0.0
            try:
                return float(raw) / 100.0
            except ValueError:
                return 0.0
        return {
            "white": pct("pctwhite"), "black": pct("pctblack"),
            "api": pct("pctapi"), "aian": pct("pctaian"),
            "mult_other": pct("pct2prace"), "hispanic": pct("pcthispanic"),
        }

    def probabilities_for(self, surname: str) -> Optional[Dict[str, float]]:
        """P(race | surname) for the six BISG categories, or None if the
        table itself could not be loaded (INDETERMINATE upstream --
        never fabricated). An unmatched surname (not on the list) still
        returns a real value: the national population distribution."""
        if not self._load():
            return None
        row = self._table.get(surname.strip().upper()) if surname else None
        if row is not None:
            return row
        return self._national


class CensusGeocoder:
    """Real, live, key-free geocoding (address -> Census tract FIPS)."""

    def geocode_to_tract(self, address: str) -> Optional[Dict[str, str]]:
        """Returns {"state": "24", "county": "033", "tract": "800102"}
        or None if the address didn't resolve or the service was
        unreachable -- never a guessed geography."""
        import json
        import urllib.parse

        params = urllib.parse.urlencode({
            "address": address, "benchmark": "Public_AR_Current",
            "vintage": "Current_Current", "format": "json",
        })
        try:
            # nosec B310 -- _GEOCODER_URL is a fixed https:// constant;
            # `params` is built via urllib.parse.urlencode, which
            # percent-encodes every value, so the address argument can
            # never alter the URL's scheme or host, only the query
            # string content after it.
            with urlopen(f"{_GEOCODER_URL}?{params}", timeout=15) as resp:  # nosec B310
                payload = json.loads(resp.read())
        except Exception:
            return None
        matches = payload.get("result", {}).get("addressMatches", [])
        if not matches:
            return None
        geogs = matches[0].get("geographies", {})
        for key, entries in geogs.items():
            if "Census Tracts" in key and entries:
                g = entries[0]
                return {"state": g["STATE"], "county": g["COUNTY"], "tract": g["TRACT"]}
        return None


class AcsRaceTable:
    """Real, live ACS B03002 race/ethnicity queries -- both the specific
    tract's counts and the (cached-per-instance) national totals the
    BISG formula's denominator needs. Requires CENSUS_API_KEY."""

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key or os.environ.get("CENSUS_API_KEY")
        self._national_cache: Optional[Dict[str, float]] = None

    def _query(self, for_clause: str, in_clause: Optional[str] = None) -> Optional[list]:
        if not self.api_key:
            return None
        import urllib.parse
        params = {"get": "NAME," + ",".join(_ACS_VARS), "for": for_clause,
                  "key": self.api_key}
        if in_clause:
            params["in"] = in_clause
        url = f"{_ACS_URL}?{urllib.parse.urlencode(params)}"
        try:
            # nosec B310 -- _ACS_URL is a fixed https:// constant; url
            # only appends a urlencode()'d (percent-encoded) query
            # string, which cannot change the scheme or host, only the
            # query content -- same justification as geocode_to_tract's
            # urlopen above.
            with urlopen(url, timeout=15) as resp:  # nosec B310
                import json
                rows = json.loads(resp.read())
        except Exception:
            return None
        if len(rows) < 2:
            return None
        return rows[1]

    @staticmethod
    def _counts_from_row(row: list) -> Dict[str, float]:
        # Order matches _ACS_VARS: total, nh_white, nh_black, nh_aian,
        # nh_asian, nh_nhpi, nh_other, nh_two_or_more, hispanic.
        vals = [float(v) if v not in (None, "") else 0.0 for v in row[1:10]]
        total, nh_white, nh_black, nh_aian, nh_asian, nh_nhpi, nh_other, nh_two, hisp = vals
        return {
            "total": total, "white": nh_white, "black": nh_black, "aian": nh_aian,
            # api combines Asian-alone and NHPI-alone -- CFPB treats these
            # as one category too (see module docstring).
            "api": nh_asian + nh_nhpi,
            # mult_other combines "some other race alone" and "two or more
            # races" directly (documented simplification vs. CFPB's
            # proportional Word-2008 redistribution -- see module docstring).
            "mult_other": nh_other + nh_two,
            "hispanic": hisp,
        }

    def national_totals(self) -> Optional[Dict[str, float]]:
        if self._national_cache is not None:
            return self._national_cache
        row = self._query("us:1")
        if row is None:
            return None
        self._national_cache = self._counts_from_row(row)
        return self._national_cache

    def tract_counts(self, state: str, county: str, tract: str) -> Optional[Dict[str, float]]:
        row = self._query(f"tract:{tract}", f"state:{state} county:{county}")
        if row is None:
            return None
        return self._counts_from_row(row)


class CensusBISGEstimator(BISGEstimator):
    """The real, live-data-backed reference implementation. Requires
    CENSUS_API_KEY for the geography step; the surname step needs only
    network access (downloads and caches the real Census surname list
    on first use). Any missing piece -> INDETERMINATE, never a
    fabricated distribution."""

    def __init__(self, surname_table: Optional[SurnameTable] = None,
                 geocoder: Optional[CensusGeocoder] = None,
                 acs: Optional[AcsRaceTable] = None):
        self.surname_table = surname_table or SurnameTable()
        self.geocoder = geocoder or CensusGeocoder()
        self.acs = acs or AcsRaceTable()

    def estimate(self, surname: Optional[str], address: Optional[str]) -> BISGEstimate:
        if not surname or not address:
            return BISGEstimate(
                distribution=None,
                indeterminate_reason="both surname and address are required for BISG",
            )

        name_pr = self.surname_table.probabilities_for(surname)
        if name_pr is None:
            return BISGEstimate(
                distribution=None,
                indeterminate_reason="surname reference table unavailable "
                                     "(network unreachable or download failed)",
            )

        tract = self.geocoder.geocode_to_tract(address)
        if tract is None:
            return BISGEstimate(
                distribution=None,
                indeterminate_reason="address did not geocode to a census tract "
                                     "(unreachable, or address did not resolve)",
            )

        national = self.acs.national_totals()
        tract_counts = self.acs.tract_counts(**tract)
        if national is None or tract_counts is None:
            return BISGEstimate(
                distribution=None,
                indeterminate_reason="ACS tract/national race data unavailable "
                                     "(CENSUS_API_KEY missing/invalid, or API "
                                     "unreachable)",
            )

        u = {}
        for r in RACE_CATEGORIES:
            national_r = national.get(r, 0.0)
            here_given_r = (tract_counts.get(r, 0.0) / national_r) if national_r > 0 else 0.0
            u[r] = name_pr.get(r, 0.0) * here_given_r
        total_u = sum(u.values())
        if total_u <= 0:
            return BISGEstimate(
                distribution=None,
                indeterminate_reason="combined name/geography likelihood is zero "
                                     "for every category -- cannot normalize a "
                                     "posterior",
            )
        posterior = {r: u[r] / total_u for r in RACE_CATEGORIES}
        return BISGEstimate(distribution=posterior, precision="tract")


class FakeBISGEstimator(BISGEstimator):
    """Deterministic test double. Returns a fixed, injected distribution
    (or an INDETERMINATE result) regardless of input -- for testing the
    CHECKER/rollup/sealed-channel machinery around BISG, not BISG's own
    statistical accuracy (CensusBISGEstimator, proven against real data
    in Tests/test_bisg_estimator_live.py, is what that claim rests on)."""

    def __init__(self, fixed: Optional[Dict[str, float]] = None,
                 indeterminate_reason: Optional[str] = None):
        self.fixed = fixed
        self.indeterminate_reason = indeterminate_reason

    def estimate(self, surname: Optional[str], address: Optional[str]) -> BISGEstimate:
        if self.indeterminate_reason is not None:
            return BISGEstimate(distribution=None,
                                indeterminate_reason=self.indeterminate_reason)
        return BISGEstimate(distribution=dict(self.fixed), precision="fake")
