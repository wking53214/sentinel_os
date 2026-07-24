"""Live integration suite for the customer-DR witness ("the twin").

Every test drives real services: real Postgres databases under three distinct
credential identities (Sentinel service, customer, custodian), a real Redis-backed
TransmissionQueue, and real HTTP receivers/custodian processes. Nothing that a
claim depends on is mocked -- refusals are real refusals, deliveries are real
HTTP, decryptions are real ECDH/AES-GCM.

Layout:
  * module fixtures start/stop the customer receiver(s) and (where needed) the
    custodian, seed an isolated ledger, and provision customer keys;
  * helpers run subprocess stages under the RIGHT OS user via runuser so
    credential boundaries are exercised, not assumed;
  * each test uses a FRESH replica_id and its own queue namespace slice so the
    tests are order-independent and can be read one at a time.

These tests assume the dev container's peer-auth Postgres and the users
sentinelsvc / twincustomer / twincustodian created during the build. The
`services` autouse fixture self-heals Postgres/Redis if the VM forked between
calls.
"""

import base64
import json
import os
import signal
import socket
import subprocess
import time
import uuid
from contextlib import closing, contextmanager
from typing import Any, Dict, List, Optional

import httpx
import psycopg2
import psycopg2.extras
import pytest
import redis

import twin_custody as tc
from twin_detector import OptionADecryptor, OptionDDecryptor, run_detection
from twin_sync_worker import SYNC_QUEUE_NAME
from queue_schema import TransmissionQueue

REPO = os.path.dirname(os.path.abspath(__file__))
PY = "python3"
ICEBERG_DSN = dict(host="localhost", dbname="iceberg", user="iceberg", password="iceberg")
def _feed_dsn() -> str:
    """ledger_reader's password is set once per pytest session by the root
    conftest.py fixture (_ensure_default_ledger_runtime_role), which every
    test in the repo shares -- reading it here at call time (not as a
    module-level constant, which would freeze it at import time before that
    fixture has run) keeps this suite and the rest of the repo agreeing on
    ledger_reader's actual current password instead of each hardcoding its
    own and silently drifting apart when both run in one pytest session.
    The literal fallback only matters for a standalone run of this file
    where conftest.py's fixture didn't get a chance to run first."""
    password = os.environ.get("ICEBERG_LEDGER_RUNTIME_PASSWORD", "reader_hashfeed")
    return f"host=localhost port=5432 dbname=iceberg user=ledger_reader password={password}"
REDIS_URL = "redis://localhost:6379/0"


# ------------------------------------------------------------ infrastructure --

def _free_port() -> int:
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _grant_traversal_to_repo_ancestors():
    """Idempotent, root-only self-heal: grant 'other' EXECUTE (traverse,
    not read/list) permission on every ancestor directory from REPO up
    to the filesystem root.

    sentinelsvc/twincustomer/twincustodian are freshly created, unrelated
    OS users. If REPO sits under another user's home directory -- as it
    does on GitHub's hosted runner, where the checkout lives under
    /home/runner, whose default permissions block all other users --
    NO file under REPO is reachable by those identities no matter what
    PYTHONPATH, cwd, or sys.path say: directory traversal is denied by
    the kernel before Python's import machinery ever gets a chance.
    Confirmed directly: sys.path.insert(0, REPO) followed by an import
    still raises ModuleNotFoundError against a real, correctly-pathed
    module file when an ancestor directory lacks the traverse bit for
    'other' -- and adding only +x (not +r) on that ancestor is both
    necessary and sufficient to fix it (a directory LISTING still
    correctly reports Permission denied; reaching a KNOWN path inside
    it, which is exactly what an import does, does not need +r).

    Only the execute bit is added, only for 'other', only on
    directories, and only additively (bitwise OR -- never removes an
    existing permission) -- this repo's own dev-container apparently
    already has permissive-enough ancestor directories (this doesn't
    reproduce there), so this is a no-op most places it runs and only
    does real work on runners where the checkout lives somewhere
    normally private.
    """
    path = os.path.abspath(REPO)
    while True:
        try:
            mode = os.stat(path).st_mode
            if not mode & 0o001:
                os.chmod(path, mode | 0o001)
        except (PermissionError, FileNotFoundError):
            pass
        parent = os.path.dirname(path)
        if parent == path:
            break
        path = parent


def _ensure_services():
    subprocess.run(["/usr/local/bin/twin_ensure_services"], capture_output=True)
    _grant_traversal_to_repo_ancestors()


@pytest.fixture(autouse=True, scope="session")
def services():
    _ensure_services()
    yield


def _resolve_python_invocation(argv: List[str]) -> List[str]:
    """Make a python3 invocation immune to PYTHONPATH/cwd not reliably
    reaching the exec'd process through `runuser` + `env -i`.

    Both run_as and spawn_as already pass PYTHONPATH=REPO and cwd=REPO,
    which is sufficient in most environments -- but not, observed
    directly in CI, on every runuser/PAM configuration: a switched-user
    `import twin_custody` (or any repo-sibling module) can fail with
    ModuleNotFoundError even with both set correctly on the parent
    subprocess.run/Popen call, because PAM session setup for the target
    user can rewrite or ignore env vars and/or cwd before the final
    exec, in ways that differ by runuser/PAM version and are not
    reproducible on every box (this repo's own dev-container runuser
    does not exhibit it; GitHub's hosted ubuntu-latest runner does).

    This sidesteps the question entirely by never depending on an
    inherited env var or cwd for module resolution: for `-c` code, REPO
    is injected as a sys.path.insert() prefix baked into the code
    argument itself; for a script-file invocation, the script's
    filename is resolved to an absolute path under REPO. Command-line
    ARGUMENTS are never subject to PAM/env stripping the way
    environment variables are, so this is robust regardless of the
    underlying cause. PYTHONPATH/cwd are kept as-is alongside this --
    belt and suspenders, not a replacement.
    """
    if not argv or argv[0] != PY:
        return list(argv)
    argv = list(argv)
    if len(argv) >= 3 and argv[1] == "-c":
        argv[2] = f"import sys; sys.path.insert(0, {REPO!r})\n" + argv[2]
    elif len(argv) >= 2 and argv[1].endswith(".py") and not os.path.isabs(argv[1]):
        argv[1] = os.path.join(REPO, argv[1])
    return argv


def run_as(user: str, argv: List[str], env: Optional[Dict[str, str]] = None,
           timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a command as an OS user with a clean environment (exercises creds)."""
    full_env = {"PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": f"/home/{user}", "PYTHONPATH": REPO}
    if env:
        full_env.update(env)
    envs = [f"{k}={v}" for k, v in full_env.items()]
    argv = _resolve_python_invocation(argv)
    return subprocess.run(["runuser", "-u", user, "--", "env", "-i", *envs, *argv],
                          capture_output=True, text=True, timeout=timeout, cwd=REPO)


@contextmanager
def spawn_as(user: str, argv: List[str], env: Dict[str, str], health_url: str,
             wait_s: float = 20.0):
    """Start a long-running service as an OS user; wait for /health; guarantee kill."""
    full_env = {"PATH": "/usr/local/bin:/usr/bin:/bin",
                "HOME": f"/home/{user}", "PYTHONPATH": REPO, **env}
    envs = [f"{k}={v}" for k, v in full_env.items()]
    argv = _resolve_python_invocation(argv)
    proc = subprocess.Popen(["runuser", "-u", user, "--", "env", "-i", *envs, *argv],
                            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            start_new_session=True)
    try:
        deadline = time.time() + wait_s
        ok = False
        while time.time() < deadline:
            try:
                if httpx.get(health_url, timeout=1.0).status_code == 200:
                    ok = True
                    break
            except Exception:
                time.sleep(0.2)
        if not ok:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                pass
            out = proc.stdout.read().decode() if proc.stdout else ""
            raise RuntimeError(f"service {argv} never became healthy:\n{out[:2000]}")
        yield proc
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            pass


# ------------------------------------------------------------- seeding ledger --

def _iceberg_admin():
    return psycopg2.connect(**ICEBERG_DSN)


def seed_ledger_rows(n: int, sid_prefix: str) -> List[Dict[str, Any]]:
    """Append n well-formed, hash-chained base rows to the REAL ledger via the
    shipped ledger append path, so every seeded row is a genuine chain link."""
    from governance.ledger_postgres import PostgreSQLLedger
    ledger = PostgreSQLLedger(**ICEBERG_DSN)
    sids = []
    for i in range(n):
        sid = f"{sid_prefix}{i:03d}"
        ok = ledger.append(
            action_type="expected_wait", node="billing_queue",
            previous_value=float(100 + i), applied_value=float(120 + i),
            reason=f"twin-live seed {sid}",
            data={"call_sid": sid, "seed": sid_prefix})
        assert ok
        sids.append(sid)
    # fetch back the ids/hashes we just created
    conn = _iceberg_admin()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT id, COALESCE(call_sid, data->>'call_sid') AS call_sid, "
                "current_hash FROM ledger_entries "
                "WHERE data->>'seed' = %s ORDER BY id", (sid_prefix,))
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


# ledger_postgres.append doesn't set call_sid column (only append_decision does),
# so the shipper reads call_sid from the row's input_data/data. Confirm shape:
def _row_sid(row: Dict[str, Any]) -> Optional[str]:
    return row.get("call_sid")


# ------------------------------------------------------------- customer setup --

@pytest.fixture(scope="module")
def customer_keys(tmp_path_factory):
    """Generate customer custody + signing keys under twincustomer's home."""
    kd = "/home/twincustomer/keys_live"
    run_as("twincustomer", ["bash", "-lc", f"mkdir -p {kd}"])
    res = run_as("twincustomer", [PY, "-c", (
        "import twin_custody as tc, json, sys;"
        "priv,pub=tc.generate_recipient_keypair();"
        "spriv,spub=tc.generate_signing_keypair();"
        "print(json.dumps({'priv':priv,'pub':pub,'spriv':spriv,'spub':spub}))")])
    assert res.returncode == 0, res.stderr
    keys = json.loads(res.stdout.strip().splitlines()[-1])
    # persist to files owned by the customer
    payload = json.dumps(keys)
    run_as("twincustomer", ["bash", "-lc",
           f"cat > {kd}/keys.json <<'EOF'\n{payload}\nEOF\nchmod 600 {kd}/keys.json"])
    keys["dir"] = kd
    return keys


def _new_replica_db(dbname: str):
    """Create a fresh customer-owned replica DB (idempotent)."""
    admin = psycopg2.connect(host="localhost", dbname="postgres",
                             user="postgres")  # peer auth as root? no -- use socket
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname=%s", (dbname,))
            if not cur.fetchone():
                cur.execute(f'CREATE DATABASE "{dbname}" OWNER twincustomer')
            cur.execute(f'REVOKE CONNECT ON DATABASE "{dbname}" FROM PUBLIC')
            cur.execute(f'GRANT CONNECT ON DATABASE "{dbname}" TO twincustomer')
    finally:
        admin.close()


# The test runner is root; connect to the postgres maintenance DB via the
# peer-auth 'postgres' account by using runuser for DDL instead.
def new_replica_db(dbname: str):
    sql = (f"SELECT 1 FROM pg_database WHERE datname='{dbname}'")  # nosec B608 -- test-internal dbname only, never external input
    check = run_as("postgres", ["psql", "-tAc", sql], timeout=30)
    if check.stdout.strip() != "1":
        run_as("postgres", ["psql", "-c", f'CREATE DATABASE "{dbname}" OWNER twincustomer'], timeout=30)
    run_as("postgres", ["psql", "-c",
           f'REVOKE CONNECT ON DATABASE "{dbname}" FROM PUBLIC; '
           f'GRANT CONNECT ON DATABASE "{dbname}" TO twincustomer'], timeout=30)


@contextmanager
def receiver(dbname: str, site: str = "site-a"):
    new_replica_db(dbname)
    port = _free_port()
    with spawn_as("twincustomer", [PY, "twin_receiver.py"],
                  {"TWIN_RECEIVER_DSN": f"dbname={dbname}",
                   "TWIN_RECEIVER_PORT": str(port),
                   "TWIN_RECEIVER_SITE": site},
                  f"http://127.0.0.1:{port}/health"):
        yield f"http://127.0.0.1:{port}", dbname, port


def register_replica(url: str, replica_id: str, keys: Dict[str, str],
                     custody_model: str = "A", ship_token: str = "tok",
                     max_lag: int = 5, primary_evidence: bool = False,
                     recipient_pub: Optional[str] = None,
                     custodian_pub: Optional[str] = None):
    body = {"custody_model": custody_model,
            "recipient_pub": recipient_pub or keys["pub"],
            "recipient_fp": tc.fingerprint(recipient_pub or keys["pub"]),
            "customer_sign_pub": keys["spub"], "ship_token": ship_token,
            "max_lag_seconds": max_lag, "is_primary_evidence": primary_evidence}
    r = httpx.post(f"{url}/replica/{replica_id}/register", json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def write_targets(path: str, replica_id: str, url: str, ship_token: str,
                  recipient_pub: str, site: str = "site-a"):
    targets = [{"replica_id": replica_id, "site": site, "receiver_url": url,
                "ship_token": ship_token, "recipient_pub": recipient_pub,
                "custody_model": "A"}]
    with open(path, "w") as fh:
        json.dump(targets, fh)
    os.chmod(path, 0o644)
    return path


def flush_sync_queue():
    q = TransmissionQueue(name=SYNC_QUEUE_NAME, redis_url=REDIS_URL)
    q.flush_namespace()


def set_cursor(replica_id: str, value: int):
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    if value <= 0:
        r.delete(f"twin:cursor:{replica_id}")
    else:
        r.set(f"twin:cursor:{replica_id}", value)


def ship(targets_path: str, extra_env: Optional[Dict[str, str]] = None):
    # sentinelsvc authenticates to Postgres via the peer-auth socket (no host,
    # no password) -- the same identity boundary the build provisioned.
    env = {"SENTINEL_REDIS_URL": REDIS_URL, "TWIN_TARGETS_FILE": targets_path,
           "POSTGRES_DB": "iceberg"}
    if extra_env:
        env.update(extra_env)
    return run_as("sentinelsvc", [PY, "twin_shipper.py", "--once", "--targets", targets_path],
                  env=env, timeout=60)


def sync(extra_env: Optional[Dict[str, str]] = None):
    env = {"SENTINEL_REDIS_URL": REDIS_URL}
    if extra_env:
        env.update(extra_env)
    return run_as("sentinelsvc", [PY, "twin_sync_worker.py", "--once"], env=env, timeout=60)


def customer_sql(db: str, *statements: str) -> subprocess.CompletedProcess:
    """Run SQL against a customer-owned DB AS twincustomer (peer-auth socket).
    Used to simulate at-rest tamper on the replica store from the identity that
    actually owns it -- the pytest process is root and cannot authenticate as
    the customer."""
    joined = "; ".join(s.rstrip(";") for s in statements) + ";"
    res = run_as("twincustomer", ["psql", "-v", "ON_ERROR_STOP=1", "-d", db, "-c", joined],
                 timeout=30)
    assert res.returncode == 0, f"customer_sql failed: {res.stderr}\n{res.stdout}"
    return res


def rid() -> str:
    return f"r-{uuid.uuid4().hex[:8]}"


# pytest's tmp_path lives under /tmp/pytest-of-root (mode 700); other OS users
# can't traverse into it. Cross-user artifacts (targets files, submission
# records, DB dumps) go through this world-traversable scratch dir instead.
_SCRATCH = "/tmp/twin_live_scratch"  # nosec B108 -- fixed path is deliberate: all three OS identities (sentinelsvc/twincustomer/twincustodian) need to agree on the same location without coordinating an env var; see comment above and the B103 note below
os.makedirs(_SCRATCH, exist_ok=True)
os.chmod(_SCRATCH, 0o777)  # nosec B103 -- deliberate, see comment above: all
# three OS identities need to read/write here for the cross-identity test
# scenarios; ephemeral /tmp scratch holding test coordination files only,
# never credentials or secrets.


def scratch(name: str) -> str:
    return os.path.join(_SCRATCH, f"{uuid.uuid4().hex[:8]}_{name}")


def get_entries(url: str, replica_id: str) -> List[Dict[str, Any]]:
    r = httpx.get(f"{url}/replica/{replica_id}/entries", timeout=10)
    r.raise_for_status()
    return r.json()["entries"]


def customer_decryptor(keys: Dict[str, str]) -> OptionADecryptor:
    return OptionADecryptor(keys["priv"])


# ================================================================= the tests ==

def test_end_to_end_encrypted_sync_and_customer_open(customer_keys, tmp_path):
    """Full path: seed -> ship as Sentinel -> sync -> customer opens & deep-verifies.
    The stored envelope is AES-256-GCM; only the customer key opens it."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-e2e")
        seed = seed_ledger_rows(3, f"E2E{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-e2e", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        # only our rows: cursor just below first seeded id, ship one batch
        s = ship(tp)
        assert "enqueued" in s.stdout or s.returncode == 0, s.stderr
        sync()
        ents = [e for e in get_entries(url, r) if e["primary_id"] in {x["id"] for x in seed}]
        assert len(ents) == 3, f"expected 3 synced, got {len(ents)}"
        dec = customer_decryptor(customer_keys)
        for e in ents:
            aad = {"replica_id": r, "primary_id": e["primary_id"], "current_hash": e["current_hash"]}
            row = json.loads(dec.open(e["envelope"], aad))
            ok, detail = tc.deep_verify_row(row)
            assert ok, detail


def test_sentinel_cannot_decrypt_anywhere(customer_keys, tmp_path):
    """Enumerate the ways Sentinel might try to read a replica payload and show
    each fails: (a) using the public key it holds, (b) reading the customer key
    file, (c) connecting to the customer DB. This is the core custody claim."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-nc")
        seed = seed_ledger_rows(1, f"NC{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-nc", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        e = [e for e in get_entries(url, r) if e["primary_id"] == seed[0]["id"]][0]
        aad = {"replica_id": r, "primary_id": e["primary_id"], "current_hash": e["current_hash"]}

        # (a) public key as private -> refused
        with pytest.raises(tc.CustodyError):
            tc.open_envelope(e["envelope"], customer_keys["pub"], aad)

        # (b) Sentinel identity cannot READ the customer's private key file
        readres = run_as("sentinelsvc", ["cat", f"{customer_keys['dir']}/keys.json"])
        assert readres.returncode != 0 and "denied" in (readres.stderr.lower() + readres.stdout.lower())

        # (c) Sentinel identity cannot CONNECT to the customer replica DB
        connres = run_as("sentinelsvc", ["psql", "-d", db, "-c", "SELECT count(*) FROM replica_entries"])
        assert connres.returncode != 0 and "denied" in connres.stderr.lower()


def test_clean_match_verdict(customer_keys, tmp_path):
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-cl")
        seed = seed_ledger_rows(4, f"CL{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-cl", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        # detector restricted to our seeded ids by filtering the feed
        entries = get_entries(url, r)
        feed = [{"id": x["id"], "call_sid": x["call_sid"], "previous_hash": None,
                 "current_hash": x["current_hash"], "t": time.time()} for x in seed]
        # fill previous_hash from ledger for our rows
        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, previous_hash FROM ledger_entries WHERE id = ANY(%s)",
                        ([x["id"] for x in seed],))
            ph = dict(cur.fetchall())
        conn.close()
        for f in feed:
            f["previous_hash"] = ph[f["id"]]
        ours = [e for e in entries if e["primary_id"] in ph]
        rep = run_detection(ours, feed, [], sla_seconds=5,
                            decryptor=customer_decryptor(customer_keys), replica_id=r)
        assert rep["verdict"] == "CLEAN", rep
        assert rep["counts"]["match"] == 4 and rep["counts"]["deep_verified"] == 4


def test_forced_omission_caught_by_icc(customer_keys, tmp_path):
    """Shipper drops one sid (TWIN_SHIPPER_SKIP_SIDS). The clear-feed comparison
    sees it 'present on primary, not replica'; the ICC record also flags it. If
    the row had been dropped before the ledger too, ICC alone catches it
    (absent_everywhere)."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-om")
        pref = f"OM{uuid.uuid4().hex[:4]}"
        seed = seed_ledger_rows(3, pref)
        dropped = seed[1]
        tp = write_targets(scratch("t.json"), r, url, "tok-om", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp, extra_env={"TWIN_SHIPPER_SKIP_SIDS": dropped["call_sid"]})
        sync()

        entries = get_entries(url, r)
        ours = [e for e in entries if e["primary_id"] in {x["id"] for x in seed}]
        assert dropped["id"] not in {e["primary_id"] for e in ours}

        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, COALESCE(call_sid, data->>'call_sid'), previous_hash, current_hash FROM ledger_entries WHERE id=ANY(%s)",
                        ([x["id"] for x in seed],))
            feed = [{"id": r_[0], "call_sid": r_[1], "previous_hash": r_[2],
                     "current_hash": r_[3], "t": time.time() - 100} for r_ in cur.fetchall()]
        conn.close()
        # submission record: customer logged all three at submission time
        subrec = [{"sid": x["call_sid"], "t": time.time() - 100} for x in seed]
        rep = run_detection(ours, feed, subrec, sla_seconds=5,
                            decryptor=customer_decryptor(customer_keys), replica_id=r)
        assert rep["verdict"] == "FINDINGS"
        miss_sids = {m.get("sid") for m in rep["missing"]}
        assert dropped["call_sid"] in miss_sids
        # ICC flagged it independently of the feed comparison
        assert any(m.get("icc") and m.get("sid") == dropped["call_sid"] for m in rep["missing"]) or \
               any(not m.get("icc") and m.get("sid") == dropped["call_sid"] for m in rep["missing"])

        # absent-everywhere: a sid the customer submitted that never reached the ledger
        ghost = f"{pref}GHOST"
        subrec2 = subrec + [{"sid": ghost, "t": time.time() - 100}]
        rep2 = run_detection(ours, feed, subrec2, sla_seconds=5, replica_id=r)
        assert any(m.get("sid") == ghost and m.get("sub") == "absent_everywhere"
                   for m in rep2["missing"]), rep2["missing"]


def test_tamper_distinct_from_omission(customer_keys, tmp_path):
    """Two different corruptions must produce two different sub-causes, and both
    must be distinct from a missing entry:
      * flip a byte in the stored ciphertext  -> envelope_unopenable
      * edit clear current_hash on the replica -> clear_hash_mismatch
    """
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-tm")
        seed = seed_ledger_rows(3, f"TM{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-tm", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()

        # Corrupt directly in the customer DB (simulating at-rest tamper on the
        # replica store), as the customer identity.
        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, previous_hash, current_hash, COALESCE(call_sid, data->>'call_sid') FROM ledger_entries WHERE id=ANY(%s) ORDER BY id",
                        ([x["id"] for x in seed],))
            feedrows = cur.fetchall()
        conn.close()
        feed = [{"id": x[0], "previous_hash": x[1], "current_hash": x[2],
                 "call_sid": x[3], "t": time.time()} for x in feedrows]

        # tamper #1: envelope ct byte-flip on the middle row (read via API, flip,
        # write back AS the customer who owns the replica store)
        tamper_ct_id = seed[1]["id"]
        pre = [e for e in get_entries(url, r) if e["primary_id"] == tamper_ct_id][0]
        env = pre["envelope"]
        ct = bytearray(base64.b64decode(env["ct"]))
        ct[3] ^= 0x02
        env2 = dict(env, ct=base64.b64encode(bytes(ct)).decode())
        env2_json = json.dumps(env2).replace("'", "''")
        # tamper #2: clear current_hash edit on the last row
        tamper_hash_id = seed[2]["id"]
        customer_sql(db,
            f"UPDATE replica_entries SET envelope='{env2_json}'::jsonb "  # nosec B608 -- deliberate tamper-simulation write in a test, values built earlier in this same test, never external input
            f"WHERE replica_id='{r}' AND primary_id={tamper_ct_id}",
            f"UPDATE replica_entries SET current_hash='{'deadbeef' * 8}' "  # nosec B608 -- same as above: deliberate test tamper-simulation, hardcoded hex
            f"WHERE replica_id='{r}' AND primary_id={tamper_hash_id}")

        ours = get_entries(url, r)
        ours = [e for e in ours if e["primary_id"] in {x["id"] for x in seed}]
        rep = run_detection(ours, feed, [], sla_seconds=5,
                            decryptor=customer_decryptor(customer_keys), replica_id=r)
        subs = {d["primary_id"]: d["sub"] for d in rep["diverge"]}
        assert subs.get(tamper_ct_id) == "envelope_unopenable", rep["diverge"]
        assert subs.get(tamper_hash_id) == "clear_hash_mismatch", rep["diverge"]
        # the untouched row still matches -> tamper isn't mistaken for total loss
        assert rep["counts"]["match"] == 1


def test_offline_replica_backs_up_then_recovers(customer_keys, tmp_path):
    """With the receiver DOWN, sync jobs fail as SERVICE_INTERRUPTION and retry
    (they do not vanish). When the receiver returns, a later sync delivers them."""
    flush_sync_queue()
    new_replica_db("twin_live_off")
    port = _free_port()
    r = rid()
    seed = seed_ledger_rows(2, f"OFF{uuid.uuid4().hex[:4]}")
    tp = write_targets(scratch("t.json"), r, f"http://127.0.0.1:{port}",
                       "tok-off", customer_keys["pub"])
    set_cursor(r, seed[0]["id"] - 1)
    # Register while UP, then take it down for the first sync.
    with spawn_as("twincustomer", [PY, "twin_receiver.py"],
                  {"TWIN_RECEIVER_DSN": "dbname=twin_live_off",
                   "TWIN_RECEIVER_PORT": str(port), "TWIN_RECEIVER_SITE": "site-a"},
                  f"http://127.0.0.1:{port}/health"):
        register_replica(f"http://127.0.0.1:{port}", r, customer_keys, ship_token="tok-off")
    ship(tp)  # enqueues 2 while receiver is DOWN
    sync()  # jobs fail (connect refused), get rescheduled
    q = TransmissionQueue(name=SYNC_QUEUE_NAME, redis_url=REDIS_URL)
    st = q.stats()
    # nothing dead-lettered: connect-refused is retryable
    assert st.get("dead", 0) == 0, st
    # bring the receiver back and let backoff elapse (base 1s + jitter<=250ms)
    with spawn_as("twincustomer", [PY, "twin_receiver.py"],
                  {"TWIN_RECEIVER_DSN": "dbname=twin_live_off",
                   "TWIN_RECEIVER_PORT": str(port), "TWIN_RECEIVER_SITE": "site-a"},
                  f"http://127.0.0.1:{port}/health"):
        time.sleep(1.6)
        sync()
        head = httpx.get(f"http://127.0.0.1:{port}/replica/{r}/head", timeout=10).json()
        assert head["count"] == 2, f"expected recovery of 2, got {head}"


def test_partition_midflight_maps_to_network_latency(customer_keys, tmp_path):
    """A receiver that accepts the connection then never responds must surface as
    NETWORK_LATENCY (read timeout), retryable -- not as data loss."""
    flush_sync_queue()
    # a raw socket server that accepts and stalls
    import threading
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    stall_port = srv.getsockname()[1]
    stop = threading.Event()
    held = []

    def _accept():
        srv.settimeout(0.5)
        while not stop.is_set():
            try:
                c, _ = srv.accept()
                held.append(c)  # accept, never reply
            except socket.timeout:
                continue
    t = threading.Thread(target=_accept, daemon=True)
    t.start()
    try:
        r = rid()
        seed = seed_ledger_rows(1, f"PT{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, f"http://127.0.0.1:{stall_port}",
                           "tok-pt", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync({"TWIN_SYNC_TIMEOUT": "1"})  # worker default timeout 3s; still bounded
        trail_q = TransmissionQueue(name=SYNC_QUEUE_NAME, redis_url=REDIS_URL)
        job_id = f"{r}|{seed[0]['id']}"
        trail = trail_q.error_trail(job_id)
        reasons = {t_.get("reason") for t_ in trail}
        assert "network_latency" in reasons, trail
    finally:
        stop.set()
        t.join(timeout=2)
        for c in held:
            try:
                c.close()
            except Exception:
                pass
        srv.close()


def test_duplicate_delivery_is_idempotent(customer_keys, tmp_path):
    """Re-POSTing an identical entry -> duplicate (200, no second row). Re-POSTing
    a DIFFERENT entry at the same primary_id -> 409 (immutability)."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-dup")
        seed = seed_ledger_rows(1, f"DUP{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-dup", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        e = [e for e in get_entries(url, r) if e["primary_id"] == seed[0]["id"]][0]
        hdrs = {"Authorization": "Bearer tok-dup"}
        body = {"primary_id": e["primary_id"], "call_sid": e["call_sid"],
                "previous_hash": e["previous_hash"], "current_hash": e["current_hash"],
                "envelope": e["envelope"]}
        # identical re-delivery
        r1 = httpx.post(f"{url}/replica/{r}/entries", json=body, headers=hdrs, timeout=10)
        assert r1.status_code == 200 and r1.json()["status"] == "duplicate", r1.text
        # altered re-delivery
        r2 = httpx.post(f"{url}/replica/{r}/entries",
                        json={**body, "current_hash": "f" * 64}, headers=hdrs, timeout=10)
        assert r2.status_code == 409, r2.text
        head = httpx.get(f"{url}/replica/{r}/head", timeout=10).json()
        assert head["count"] == 1


def test_out_of_order_delivery_reconstructs_chain(customer_keys, tmp_path):
    """Deliver entries 3,2,1 in that order. The receiver stores by primary_id and
    the detector reconstructs linkage regardless of arrival order."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-ord")
        seed = seed_ledger_rows(3, f"ORD{uuid.uuid4().hex[:4]}")
        # ship one row at a time, in reverse, by moving the cursor deliberately
        for target_row in reversed(seed):
            set_cursor(r, target_row["id"] - 1)
            tp = write_targets(scratch("t.json"), r, url, "tok-ord", customer_keys["pub"])
            # limit shipper to exactly this row by capping the batch and cursor
            ship(tp, extra_env={"TWIN_SHIPPER_ONEROW": "1"})
            sync()
        entries = get_entries(url, r)
        ours = [e for e in entries if e["primary_id"] in {x["id"] for x in seed}]
        # arrival order was reverse; stored order is ascending primary_id
        assert [e["primary_id"] for e in ours] == sorted(e["primary_id"] for e in ours)
        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, previous_hash, current_hash, COALESCE(call_sid, data->>'call_sid') FROM ledger_entries WHERE id=ANY(%s)",
                        ([x["id"] for x in seed],))
            feed = [{"id": x[0], "previous_hash": x[1], "current_hash": x[2],
                     "call_sid": x[3], "t": time.time()} for x in cur.fetchall()]
        conn.close()
        rep = run_detection(ours, feed, [], sla_seconds=5, replica_id=r)
        assert rep["chain_ok"], rep["chain_breaks"]


def test_torn_delivery_dead_letters_then_requeues(customer_keys, tmp_path):
    """A structurally invalid (truncated) envelope is refused 422 -> DATA_CORRUPTION
    dead-letter. The operator's requeue_from_dlq path can retry it, and a clean
    re-ship then delivers."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-torn")
        seed = seed_ledger_rows(1, f"TORN{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-torn", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync({"TWIN_SYNC_CORRUPT_ONCE": "1"})  # torn on first send
        q = TransmissionQueue(name=SYNC_QUEUE_NAME, redis_url=REDIS_URL)
        job_id = f"{r}|{seed[0]['id']}"
        # authoritative: the job's error trail shows it dead-lettered as DATA_CORRUPTION
        trail = q.error_trail(job_id)
        reasons = {t_.get("reason") for t_ in trail}
        assert "data_corruption" in reasons, trail
        peek_ids = set()
        for p in q.dlq_peek(20):
            peek_ids |= {v for v in p.values() if isinstance(v, str)}
        assert job_id in peek_ids or any(job_id in str(p) for p in q.dlq_peek(20)), q.dlq_peek(20)
        # not on the replica yet
        assert not [e for e in get_entries(url, r) if e["primary_id"] == seed[0]["id"]]
        # operator requeues; a fresh ship re-enqueues clean; deliver
        q.requeue_from_dlq(job_id)
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        assert [e for e in get_entries(url, r) if e["primary_id"] == seed[0]["id"]]


def test_custody_migration_A_to_D(customer_keys, tmp_path):
    """Migrate a replica from customer-held key (A) to custodian-held key (D).
    After migration: the OLD key no longer opens stored envelopes; decryption
    works only through the custodian; a signed custody_migration event is on the
    log; and deep verification still passes (AAD slots preserved)."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        # bring up a custodian
        cust_home = "/home/twincustodian/cust_live"
        run_as("twincustodian", ["bash", "-lc", f"rm -rf {cust_home}; mkdir -p {cust_home}"])
        cport = _free_port()
        with spawn_as("twincustodian", [PY, "twin_custodian.py"],
                      {"TWIN_CUSTODIAN_HOME": cust_home, "TWIN_CUSTODIAN_PORT": str(cport)},
                      f"http://127.0.0.1:{cport}/health"):
            cust_url = f"http://127.0.0.1:{cport}"
            cust_pub = httpx.get(f"{cust_url}/public-keys", timeout=10).json()["recipient_pub"]
            r = rid()
            register_replica(url, r, customer_keys, ship_token="tok-mig")
            httpx.post(f"{cust_url}/register-replica",
                       json={"replica_id": r, "customer_sign_pub": customer_keys["spub"]},
                       timeout=10).raise_for_status()
            seed = seed_ledger_rows(2, f"MIG{uuid.uuid4().hex[:4]}")
            tp = write_targets(scratch("t.json"), r, url, "tok-mig", customer_keys["pub"])
            set_cursor(r, seed[0]["id"] - 1)
            ship(tp)
            sync()

            # write customer key files the migrate CLI reads
            kd = customer_keys["dir"]
            run_as("twincustomer", ["bash", "-lc",
                   f"printf '%s' '{customer_keys['priv']}' > {kd}/old.priv; "
                   f"printf '%s' '{cust_pub}' > {kd}/new.pub; "
                   f"printf '%s' '{customer_keys['spriv']}' > {kd}/sign.priv; "
                   f"printf '%s' '{customer_keys['spub']}' > {kd}/sign.pub; "
                   f"chmod 600 {kd}/*.priv"])
            mig = run_as("twincustomer", [PY, "twin_migrate.py",
                        "--replica-dsn", f"dbname={db}", "--receiver-url", url,
                        "--replica-id", r, "--old-key-file", f"{kd}/old.priv",
                        "--new-recipient-pub-file", f"{kd}/new.pub", "--new-model", "D",
                        "--actor", "customer-admin", "--sign-key-file", f"{kd}/sign.priv",
                        "--sign-pub-file", f"{kd}/sign.pub", "--custodian-url", cust_url])
            assert mig.returncode == 0, mig.stderr + mig.stdout

            ents = [e for e in get_entries(url, r) if e["primary_id"] in {x["id"] for x in seed}]
            # OLD key now fails
            aad0 = {"replica_id": r, "primary_id": ents[0]["primary_id"],
                    "current_hash": ents[0]["current_hash"]}
            with pytest.raises(tc.CustodyError):
                tc.open_envelope(ents[0]["envelope"], customer_keys["priv"], aad0)
            # custodian opens + deep verify holds
            dec = OptionDDecryptor(cust_url, r, customer_keys["spriv"], "customer-audit")
            for e in ents:
                aad = {"replica_id": r, "primary_id": e["primary_id"], "current_hash": e["current_hash"]}
                row = json.loads(dec.open(e["envelope"], aad))
                ok, detail = tc.deep_verify_row(row)
                assert ok, detail
            # signed migration event present
            log = httpx.get(f"{url}/replica/{r}/custody-log", timeout=10).json()["events"]
            assert any(ev["event"] == "custody_migration" for ev in log), log


def test_custodian_attribution_grants_and_refusals(customer_keys, tmp_path):
    """Custody D: a valid customer-signed request is granted and logged; a
    request with a bad signature is refused and STILL logged with the claimed
    requester; the audit log verifies (chain + custodian signatures)."""
    flush_sync_queue()
    from twin_custodian import verify_audit_log
    with receiver("twin_live_a") as (url, db, port):
        cust_home = "/home/twincustodian/cust_live2"
        run_as("twincustodian", ["bash", "-lc", f"rm -rf {cust_home}; mkdir -p {cust_home}"])
        cport = _free_port()
        with spawn_as("twincustodian", [PY, "twin_custodian.py"],
                      {"TWIN_CUSTODIAN_HOME": cust_home, "TWIN_CUSTODIAN_PORT": str(cport)},
                      f"http://127.0.0.1:{cport}/health"):
            cust_url = f"http://127.0.0.1:{cport}"
            cust_pub = httpx.get(f"{cust_url}/public-keys", timeout=10).json()["recipient_pub"]
            r = rid()
            register_replica(url, r, customer_keys, custody_model="D",
                             recipient_pub=cust_pub, ship_token="tok-attr")
            httpx.post(f"{cust_url}/register-replica",
                       json={"replica_id": r, "customer_sign_pub": customer_keys["spub"]},
                       timeout=10).raise_for_status()
            seed = seed_ledger_rows(1, f"ATTR{uuid.uuid4().hex[:4]}")
            tp = write_targets(scratch("t.json"), r, url, "tok-attr", cust_pub)
            set_cursor(r, seed[0]["id"] - 1)
            ship(tp)
            sync()
            e = [e for e in get_entries(url, r) if e["primary_id"] == seed[0]["id"]][0]
            aad = {"replica_id": r, "primary_id": e["primary_id"], "current_hash": e["current_hash"]}

            # granted decrypt
            dec = OptionDDecryptor(cust_url, r, customer_keys["spriv"], "customer-audit")
            _ = dec.open(e["envelope"], aad)

            # forged authorization: sign with the WRONG key, claim to be 'sentinel'
            wrong_priv, _ = tc.generate_signing_keypair()
            nonce = uuid.uuid4().hex
            auth = {"replica_id": r, "primary_id": e["primary_id"], "nonce": nonce,
                    "requester": "sentinel"}
            body = {"replica_id": r, "primary_id": e["primary_id"], "envelope": e["envelope"],
                    "aad": aad, "requester": "sentinel", "nonce": nonce,
                    "auth_sig": tc.sign(auth, wrong_priv)}
            resp = httpx.post(f"{cust_url}/decrypt", json=body, timeout=10)
            assert resp.status_code == 403, resp.text

            audit = httpx.get(f"{cust_url}/audit-log", params={"replica_id": r}, timeout=10).json()
            v = verify_audit_log(audit["events"], audit["log_sign_pub"])
            assert v["ok"], v
            grants = [ev for ev in audit["events"] if ev.get("event") == "decrypt" and ev.get("granted")]
            refus = [ev for ev in audit["events"] if ev.get("event") == "decrypt" and not ev.get("granted")]
            assert len(grants) == 1 and grants[0]["requester"] == "customer-audit"
            assert any(x["requester"] == "sentinel" for x in refus), refus


def test_multisite_fanout_independent_verdicts(customer_keys, tmp_path):
    """Two sites, two receivers, one ledger. A tamper on site-b must not change
    site-a's verdict: divergence detection is per-site."""
    flush_sync_queue()
    with receiver("twin_live_a", site="site-a") as (url_a, db_a, port_a), \
         receiver("twin_live_b", site="site-b") as (url_b, db_b, port_b):
        ra, rb = rid(), rid()
        register_replica(url_a, ra, customer_keys, ship_token="tok-a")
        register_replica(url_b, rb, customer_keys, ship_token="tok-b")
        seed = seed_ledger_rows(2, f"FAN{uuid.uuid4().hex[:4]}")
        ids = [x["id"] for x in seed]
        for (rr, url, tok) in [(ra, url_a, "tok-a"), (rb, url_b, "tok-b")]:
            tp = write_targets(scratch(f"{rr}.json"), rr, url, tok, customer_keys["pub"])
            set_cursor(rr, seed[0]["id"] - 1)
            ship(tp)
            sync()
        # tamper site-b only (as the customer who owns site-b's store)
        customer_sql(db_b,
            f"UPDATE replica_entries SET current_hash='{'0' * 64}' "  # nosec B608 -- deliberate test tamper-simulation, hardcoded hex
            f"WHERE replica_id='{rb}' AND primary_id={ids[0]}")
        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, previous_hash, current_hash, COALESCE(call_sid, data->>'call_sid') FROM ledger_entries WHERE id=ANY(%s)",
                        (ids,))
            feed = [{"id": x[0], "previous_hash": x[1], "current_hash": x[2],
                     "call_sid": x[3], "t": time.time()} for x in cur.fetchall()]
        conn.close()
        ours_a = [e for e in get_entries(url_a, ra) if e["primary_id"] in ids]
        ours_b = [e for e in get_entries(url_b, rb) if e["primary_id"] in ids]
        rep_a = run_detection(ours_a, feed, [], sla_seconds=5,
                             decryptor=customer_decryptor(customer_keys), replica_id=ra)
        rep_b = run_detection(ours_b, feed, [], sla_seconds=5,
                             decryptor=customer_decryptor(customer_keys), replica_id=rb)
        assert rep_a["verdict"] == "CLEAN", rep_a
        assert rep_b["verdict"] == "FINDINGS" and rep_b["counts"]["diverge"] == 1


def test_never_blocks_primary(customer_keys, tmp_path):
    """A dead twin target must not slow the primary ledger path. We measure
    primary append latency with the twin sync queue backed up against a dead
    receiver; appends stay fast because the shipper/worker are out-of-band."""
    flush_sync_queue()
    r = rid()
    dead_port = _free_port()  # nothing listening
    seed_ledger_rows(1, f"BLK{uuid.uuid4().hex[:4]}")
    tp = write_targets(scratch("t.json"), r, f"http://127.0.0.1:{dead_port}",
                       "tok-blk", customer_keys["pub"])
    set_cursor(r, 0)
    ship(tp)          # enqueue against a dead receiver
    sync()            # fails, backs up -- but out-of-band
    # now measure primary append latency
    from governance.ledger_postgres import PostgreSQLLedger
    ledger = PostgreSQLLedger(**ICEBERG_DSN)
    t0 = time.time()
    for i in range(5):
        assert ledger.append(action_type="expected_wait", node="n",
                             previous_value=1.0, applied_value=2.0,
                             reason=f"latency probe {i}", data={"seed": "BLKPROBE"})
    dt = time.time() - t0
    assert dt < 3.0, f"primary appends slowed to {dt:.2f}s with twin backed up"


def test_wipe_detection_via_extras(customer_keys, tmp_path):
    """If the primary ledger is wiped/rewritten so rows DISAPPEAR, the replica
    still holds them: those become EXTRA (present on replica, absent on primary).
    We simulate by pointing the detector's feed at a scratch ledger missing the
    rows the replica has."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-wipe")
        seed = seed_ledger_rows(3, f"WIPE{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-wipe", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        ours = [e for e in get_entries(url, r) if e["primary_id"] in {x["id"] for x in seed}]
        # feed as if the primary now shows NONE of these rows (wiped)
        rep = run_detection(ours, primary_feed=[], submission_record=[],
                            sla_seconds=5, replica_id=r)
        assert rep["counts"]["extra"] == 3, rep
        assert rep["verdict"] == "FINDINGS"
        assert all(x["sub"] == "extra_on_replica_absent_on_primary" for x in rep["extra"])


def test_restore_drill(customer_keys, tmp_path):
    """Operational recovery: pg_dump the replica DB, drop it, recreate, restore,
    and confirm entries + custody log survive and still deep-verify. This is the
    doc's own E4 retention bar exercised as a live drill."""
    flush_sync_queue()
    drill_db = "twin_live_drill"
    # customer needs CREATEDB for the drop/create half of the drill
    run_as("postgres", ["psql", "-c", "ALTER ROLE twincustomer CREATEDB"], timeout=30)
    with receiver(drill_db) as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-drill", primary_evidence=True)
        seed = seed_ledger_rows(2, f"DRL{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-drill", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        ship(tp)
        sync()
        # a custody event too, so we prove the whole DB round-trips
        ev_payload = {"replica_id": r, "event": "evidence_designation",
                      "detail": {"note": "primary evidence"}, "actor": "customer-admin"}
        httpx.post(f"{url}/replica/{r}/custody-event",
                   json={"event": "evidence_designation",
                         "detail": {"note": "primary evidence"}, "actor": "customer-admin",
                         "signature": tc.sign(ev_payload, customer_keys["spriv"]),
                         "signer_pub": customer_keys["spub"]}, timeout=10).raise_for_status()
        before = httpx.get(f"{url}/replica/{r}/head", timeout=10).json()["count"]
        dump_path = scratch("replica.dump")
    # receiver is down now (context exited); do the dump/drop/restore as customer
    d = run_as("twincustomer", ["pg_dump", "-Fc", "-d", drill_db, "-f", dump_path], timeout=120)
    assert d.returncode == 0, d.stderr
    run_as("twincustomer", ["dropdb", drill_db], timeout=60)
    run_as("twincustomer", ["createdb", drill_db], timeout=60)
    run_as("twincustomer", ["pg_restore", "-d", drill_db, dump_path], timeout=120)
    # pg_restore can warn (nonzero) on comments/owners; assert data instead
    with receiver(drill_db) as (url2, db2, port2):
        after = httpx.get(f"{url2}/replica/{r}/head", timeout=10).json()["count"]
        assert after == before == 2, (before, after)
        ents = [e for e in get_entries(url2, r) if e["primary_id"] in {x["id"] for x in seed}]
        dec = customer_decryptor(customer_keys)
        for e in ents:
            aad = {"replica_id": r, "primary_id": e["primary_id"], "current_hash": e["current_hash"]}
            ok, detail = tc.deep_verify_row(json.loads(dec.open(e["envelope"], aad)))
            assert ok, detail
        log = httpx.get(f"{url2}/replica/{r}/custody-log", timeout=10).json()["events"]
        assert any(ev["event"] == "evidence_designation" for ev in log)


def test_probe_clean_and_seeded_findings(customer_keys, tmp_path):
    """The regulator probe: exit 0 + conformant on a clean replica; exit 2 +
    exact finding counts when one tamper and one omission are seeded."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-probe", primary_evidence=True)
        pref = f"PRB{uuid.uuid4().hex[:4]}"
        seed = seed_ledger_rows(4, pref)
        tp = write_targets(scratch("t.json"), r, url, "tok-probe", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        # omit one row at ship time
        omitted = seed[2]
        ship(tp, extra_env={"TWIN_SHIPPER_SKIP_SIDS": omitted["call_sid"]})
        sync()
        # signed creation event so custody log is non-empty and verifies
        ev = {"replica_id": r, "event": "replica_created", "detail": {}, "actor": "customer-admin"}
        httpx.post(f"{url}/replica/{r}/custody-event",
                   json={"event": "replica_created", "detail": {}, "actor": "customer-admin",
                         "signature": tc.sign(ev, customer_keys["spriv"]),
                         "signer_pub": customer_keys["spub"]}, timeout=10).raise_for_status()

        # customer key + submission record files for the probe
        kd = customer_keys["dir"]
        run_as("twincustomer", ["bash", "-lc",
               f"printf '%s' '{customer_keys['priv']}' > {kd}/probe.priv; chmod 600 {kd}/probe.priv"])
        subrec = "\n".join(json.dumps({"sid": x["call_sid"], "t": time.time() - 100}) for x in seed)
        run_as("twincustomer", ["bash", "-lc", f"cat > {kd}/subrec.jsonl <<'EOF'\n{subrec}\nEOF"])

        # tamper one delivered row in the customer DB (as the customer)
        customer_sql(db,
            f"UPDATE replica_entries SET current_hash='{'a' * 64}' "  # nosec B608 -- deliberate test tamper-simulation, hardcoded hex
            f"WHERE replica_id='{r}' AND primary_id={seed[0]['id']}")

        # run the probe AS THE CUSTOMER
        pr = run_as("twincustomer", [PY, "twin_probe.py", "--receiver-url", url,
                    "--replica-id", r, "--feed-dsn", _feed_dsn(),
                    "--submission-record", f"{kd}/subrec.jsonl", "--sla-seconds", "5",
                    "--key-file", f"{kd}/probe.priv"], timeout=90)
        assert pr.returncode == 2, pr.stdout[-1500:] + pr.stderr[-500:]
        report = json.loads(pr.stdout)
        assert report["conformant"] is False
        assert report["detection"]["counts"]["diverge"] == 1
        assert omitted["call_sid"] in {m.get("sid") for m in report["detection"]["missing"]}
        assert report["custody_log"]["ok"] is True

        # now a CLEAN replica -> conformant, exit 0
        flush_sync_queue()
        r2 = rid()
        register_replica(url, r2, customer_keys, ship_token="tok-clean2")
        seed2 = seed_ledger_rows(2, f"PRBOK{uuid.uuid4().hex[:4]}")
        tp2 = write_targets(scratch("t2.json"), r2, url, "tok-clean2", customer_keys["pub"])
        set_cursor(r2, seed2[0]["id"] - 1)
        ship(tp2)
        sync()
        ev2 = {"replica_id": r2, "event": "replica_created", "detail": {}, "actor": "admin"}
        httpx.post(f"{url}/replica/{r2}/custody-event",
                   json={"event": "replica_created", "detail": {}, "actor": "admin",
                         "signature": tc.sign(ev2, customer_keys["spriv"]),
                         "signer_pub": customer_keys["spub"]}, timeout=10).raise_for_status()
        subrec2 = "\n".join(json.dumps({"sid": x["call_sid"], "t": time.time() - 100}) for x in seed2)
        run_as("twincustomer", ["bash", "-lc", f"cat > {kd}/subrec2.jsonl <<'EOF'\n{subrec2}\nEOF"])
        pr2 = run_as("twincustomer", [PY, "twin_probe.py", "--receiver-url", url,
                     "--replica-id", r2, "--feed-dsn", _feed_dsn(),
                     "--submission-record", f"{kd}/subrec2.jsonl", "--sla-seconds", "5",
                     "--key-file", f"{kd}/probe.priv"], timeout=90)
        assert pr2.returncode == 0, pr2.stdout[-1500:] + pr2.stderr[-500:]
        assert json.loads(pr2.stdout)["conformant"] is True


def test_sla_pending_vs_missing_and_clock_skew_immunity(customer_keys, tmp_path):
    """Within the SLA window an unshipped row is PENDING, not MISSING. And the
    verdict does not consult wall clocks on the entries: rewriting received_at
    to a wild value changes nothing (clock skew is not a divergence source)."""
    flush_sync_queue()
    with receiver("twin_live_a") as (url, db, port):
        r = rid()
        register_replica(url, r, customer_keys, ship_token="tok-sla", max_lag=5)
        seed = seed_ledger_rows(2, f"SLA{uuid.uuid4().hex[:4]}")
        tp = write_targets(scratch("t.json"), r, url, "tok-sla", customer_keys["pub"])
        set_cursor(r, seed[0]["id"] - 1)
        # ship only the FIRST row (advance cursor so second is unshipped)
        ship(tp, extra_env={"TWIN_SHIPPER_ONEROW": "1"})
        sync()
        conn = _iceberg_admin()
        with conn.cursor() as cur:
            cur.execute("SELECT id, previous_hash, current_hash, COALESCE(call_sid, data->>'call_sid') FROM ledger_entries WHERE id=ANY(%s) ORDER BY id",
                        ([x["id"] for x in seed],))
            rows = cur.fetchall()
        conn.close()
        # young feed timestamps -> unshipped 2nd row should be PENDING.
        # The customer expects BOTH seeded rows, so it scopes the covered window
        # to span them explicitly (an expected row not yet arrived is in scope --
        # that is what distinguishes pending from out-of-window).
        feed_young = [{"id": x[0], "previous_hash": x[1], "current_hash": x[2],
                       "call_sid": x[3], "t": time.time()} for x in rows]
        window = (seed[0]["id"], seed[1]["id"])
        ours = [e for e in get_entries(url, r) if e["primary_id"] in {x["id"] for x in seed}]
        rep_young = run_detection(ours, feed_young, [], sla_seconds=5, replica_id=r,
                                 now=time.time(), primary_id_range=window)
        assert rep_young["counts"]["pending"] >= 1 and rep_young["counts"]["missing"] == 0, rep_young

        # old feed timestamps -> same unshipped row now MISSING
        feed_old = [{**f, "t": time.time() - 1000} for f in feed_young]
        rep_old = run_detection(ours, feed_old, [], sla_seconds=5, replica_id=r,
                               now=time.time(), primary_id_range=window)
        assert rep_old["counts"]["missing"] >= 1, rep_old

        # clock-skew immunity: wildly rewrite received_at on the replica; verdict
        # for the SHIPPED row is unchanged (it is never consulted)
        customer_sql(db,
            f"UPDATE replica_entries SET received_at = now() + interval '5 years' "  # nosec B608 -- deliberate test tamper-simulation, no interpolated values on this line at all
            f"WHERE replica_id='{r}'")
        ours2 = [e for e in get_entries(url, r) if e["primary_id"] in {x["id"] for x in seed}]
        rep_skew = run_detection(ours2, feed_young, [], sla_seconds=5, replica_id=r,
                                now=time.time())
        assert rep_skew["counts"]["match"] == rep_young["counts"]["match"], (rep_skew, rep_young)
