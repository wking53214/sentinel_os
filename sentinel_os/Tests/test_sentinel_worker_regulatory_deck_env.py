"""sentinel_worker._regulatory_deck_from_env (2026-08-07): the
env-var-gated twin_client wiring that makes RegulatoryDeck's cohort-
equity escalation (dimensions 4-6) ready to activate the moment a real
twin deployment and credentials exist, without touching this repo's
own deploy configs -- none of which run a twin receiver today (checked
docker-compose.yml/-prod.yml, k8s/, Deploy/: zero references).

Extracted into its own function (mirrors _harness_config_from_env)
specifically so this is testable without running main()'s blocking
worker loop. Tested against a real ledger, same convention every other
ledger-backed test file in this session already established.
"""
import pytest

from sentinel_worker import _regulatory_deck_from_env

_ENV_VARS = ("SENTINEL_TWIN_REPLICA_ID", "SENTINEL_TWIN_RECEIVER_URL",
            "SENTINEL_SHIP_TOKEN")


@pytest.fixture(autouse=True)
def _clean_twin_env(monkeypatch):
    """Every test starts from a known-clean slate regardless of what
    the ambient environment happens to have set."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_no_env_vars_set_twin_client_stays_none(test_ledger):
    deck = _regulatory_deck_from_env(test_ledger)
    assert deck._twin_client is None
    assert deck._replica_id is None


def test_partial_env_vars_still_stays_none(test_ledger, monkeypatch):
    """All three or nothing -- two of three set must not half-activate
    it (fetch_latest_cohort_review needs both twin_client and
    replica_id together; a partial config silently constructing a
    twin_client with no replica_id, or vice versa, would be worse than
    leaving it off)."""
    monkeypatch.setenv("SENTINEL_TWIN_REPLICA_ID", "replica-1")
    monkeypatch.setenv("SENTINEL_SHIP_TOKEN", "test-token")
    # SENTINEL_TWIN_RECEIVER_URL deliberately left unset.
    deck = _regulatory_deck_from_env(test_ledger)
    assert deck._twin_client is None
    assert deck._replica_id == "replica-1"


def test_all_three_env_vars_set_activates_a_real_twin_client(test_ledger, monkeypatch):
    monkeypatch.setenv("SENTINEL_TWIN_REPLICA_ID", "replica-1")
    monkeypatch.setenv("SENTINEL_TWIN_RECEIVER_URL", "https://twin.example.test")
    monkeypatch.setenv("SENTINEL_SHIP_TOKEN", "test-token-abc")

    deck = _regulatory_deck_from_env(test_ledger)

    import httpx
    assert isinstance(deck._twin_client, httpx.Client)
    assert deck._replica_id == "replica-1"
    assert str(deck._twin_client.base_url) == "https://twin.example.test"
    assert deck._twin_client.headers["authorization"] == "Bearer test-token-abc"
    deck._twin_client.close()


def test_the_cfpb_lens_is_inserted_live_and_on_the_record(test_ledger):
    deck = _regulatory_deck_from_env(test_ledger)
    active = deck.active()
    assert len(active) == 1
    assert active[0]["mode"] == "live"
    assert active[0]["inserted_by"] == "sentinel_worker:mortgage"
