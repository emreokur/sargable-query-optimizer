import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import db

BACKEND = Path(__file__).parents[1]
CANARY = "Canary-PW-90210"


def probe(code, **env):
    base = {k: v for k, v in os.environ.items() if k not in ("DATABASE_URL", "SARGABLE_SANDBOX", "SARGABLE_ALLOWED_HOSTS",
                                                            "SARGABLE_DEMO_URL", "SARGABLE_STATIC",
                                                            "SARGABLE_LOCAL_HOSTS")}
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env={**base, **env},
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out.strip().splitlines()[-1])


MODE = "import json, db; print(json.dumps([db.URL, db.DEMO, db.SANDBOX]))"
REMOTE = f"postgresql://app:{CANARY}@db.example.com/prod?sslmode=require"


@pytest.mark.parametrize("env,expected", [
    ({}, ["postgresql://postgres:postgres@localhost:5432/vet", True, True]),
    ({"SARGABLE_SANDBOX": "1"}, ["postgresql://postgres:postgres@localhost:5432/vet", True, True]),
    ({"DATABASE_URL": REMOTE}, [REMOTE, False, False]),
    ({"DATABASE_URL": REMOTE, "SARGABLE_SANDBOX": "0"}, [REMOTE, False, False]),
    ({"DATABASE_URL": REMOTE, "SARGABLE_SANDBOX": "true"}, [REMOTE, False, False]),
    ({"DATABASE_URL": REMOTE, "SARGABLE_SANDBOX": "1"}, [REMOTE, False, True]),
    ({"DATABASE_URL": "postgresql://postgres:postgres@localhost:5432/vet"},
     ["postgresql://postgres:postgres@localhost:5432/vet", False, False]),
])
def test_s1_mode_constants_from_environment(env, expected):
    assert probe(MODE, **env) == expected


HOSTS = """import json, main
from fastapi.testclient import TestClient
codes = {h: TestClient(main.app, base_url="http://" + h).get("/api/no-such-route").status_code
         for h in ["localhost", "127.0.0.1", "sargable.internal", "other.lan", "evil.example.com"]}
print(json.dumps([main.ALLOWED_HOSTS, codes]))"""


def test_s12_allowed_hosts_default_and_extra():
    hosts, codes = probe(HOSTS)
    assert hosts == ["localhost", "127.0.0.1"]
    assert codes == {"localhost": 404, "127.0.0.1": 404, "sargable.internal": 400, "other.lan": 400, "evil.example.com": 400}
    hosts, codes = probe(HOSTS, SARGABLE_ALLOWED_HOSTS="sargable.internal,other.lan")
    assert hosts == ["localhost", "127.0.0.1", "sargable.internal", "other.lan"]
    assert codes == {"localhost": 404, "127.0.0.1": 404, "sargable.internal": 404, "other.lan": 404, "evil.example.com": 400}


@pytest.mark.parametrize("sandbox,demo", [(True, True), (True, False), (False, False)])
def test_s1_preflight_unreachable_reports_mode_and_cannot_connect(monkeypatch, sandbox, demo):
    monkeypatch.setattr(db, "URL", f"postgresql://app_user:{CANARY}@127.0.0.1:1/appdb?connect_timeout=2")
    monkeypatch.setattr(db, "SANDBOX", sandbox)
    monkeypatch.setattr(db, "DEMO", demo)
    info = db.preflight()
    assert info == {"host": "127.0.0.1", "port": "1", "database": "appdb", "user": "app_user", "serverVersion": None,
                    "mode": "sandbox" if sandbox else "live", "demo": demo, "hypopg": False, "problems": info["problems"]}
    assert len(info["problems"]) == 1 and info["problems"][0].startswith("Cannot connect")
    assert CANARY not in json.dumps(info)


B4_SENTENCE = "DATABASE_URL is not a valid connection string. Percent-encode special characters in the password."
B4_FRAGMENTS = ["Tok3n", "3nSec", "retPW", "Sec%zz", "Sec retPW", "Sec@ret"]
MALFORMED = [
    "postgresql://app:Tok3nSec%zzretPW@127.0.0.1:1/prod",
    "postgresql://app:Tok3nSec retPW@127.0.0.1:1/prod",
    "postgresql://app:Tok3nSec@retPW@127.0.0.1:1/prod",
    "host=127.0.0.1 port=1 password=Tok3nSec retPW dbname=prod",
]


@pytest.mark.parametrize("url", MALFORMED)
@pytest.mark.parametrize("sandbox", [True, False])
def test_b4_malformed_url_is_reported_without_echoing_it(monkeypatch, url, sandbox):
    import llm
    import main
    from fastapi.testclient import TestClient
    monkeypatch.setattr(db, "URL", url)
    monkeypatch.setattr(db, "SANDBOX", sandbox)
    monkeypatch.setattr(db, "DEMO", False)
    monkeypatch.setattr(llm, "propose", lambda *a: pytest.fail("Claude must not be called"))
    info = db.preflight()
    assert {k: info[k] for k in ("host", "port", "database", "user", "problems")} == {
        "host": "", "port": "", "database": "", "user": "", "problems": [B4_SENTENCE]}
    with pytest.raises(db.Refused, match="not a valid connection string"):
        db.explain("SELECT 1")
    with pytest.raises(db.Refused):
        db.schema()
    result = db.verify_fix("SELECT 1", ["CREATE INDEX ON clinics (city)"], 1, 1.0)
    assert result.keys() == {"error"} and B4_SENTENCE in result["error"]
    client = TestClient(main.app, base_url="http://localhost", raise_server_exceptions=False)
    responses = {"connection": client.get("/api/connection"), "schema": client.get("/api/schema"),
                 "explain": client.post("/api/explain", json={"sql": "SELECT 1"}),
                 "optimize": client.post("/api/optimize", json={"sql": "SELECT 1"})}
    assert {k: r.status_code for k, r in responses.items()} == {"connection": 200, "schema": 403, "explain": 403, "optimize": 403}
    for res in responses.values():
        assert not [f for f in B4_FRAGMENTS if f in res.text]


@pytest.mark.parametrize("url", ["service=prod", "postgresql:///vet?service=prod",
                                 "host=localhost service=prod dbname=vet sslmode=disable"])
def test_b4_service_file_connections_need_tls_proof(monkeypatch, url):
    for var in ("PGHOST", "PGHOSTADDR", "PGSSLMODE"):
        monkeypatch.delenv(var, raising=False)
    assert db.tls_problem(url) is not None


# P1: the demo URL comes from SARGABLE_DEMO_URL.
DEFAULT_DEMO = "postgresql://postgres:postgres@localhost:5432/vet"
DOCKER_DEMO = "postgresql://postgres:postgres@db:5432/vet"
STARTUP_AND_RESET = ("import json, db; a = [db.URL, db.DEMO, db.SANDBOX];"
                     " db.configure('h', 5432, 'd', 'u', 'p', 'require', False, ''); db.reset();"
                     " print(json.dumps([a, [db.URL, db.DEMO, db.SANDBOX]]))")


@pytest.mark.parametrize("env,expected", [
    ({"SARGABLE_DEMO_URL": DOCKER_DEMO}, [DOCKER_DEMO, True, True]),
    ({"SARGABLE_DEMO_URL": DOCKER_DEMO, "SARGABLE_SANDBOX": "0"}, [DOCKER_DEMO, True, True]),
    ({"SARGABLE_DEMO_URL": DOCKER_DEMO, "DATABASE_URL": REMOTE}, [REMOTE, False, False]),
    ({"SARGABLE_DEMO_URL": DOCKER_DEMO, "DATABASE_URL": REMOTE, "SARGABLE_SANDBOX": "1"}, [REMOTE, False, True]),
    ({}, [DEFAULT_DEMO, True, True]),
])
def test_p1_demo_url_from_environment_and_reset_restores_it(env, expected):
    assert probe(STARTUP_AND_RESET, **env) == [expected, expected]


P1_ROUTES = """import json, main
from fastapi.testclient import TestClient
c = TestClient(main.app, base_url="http://localhost", raise_server_exceptions=False)
rs = [c.get("/api/connection"), c.get("/api/schema"), c.post("/api/explain", json={"sql": "SELECT 1"}),
      c.post("/api/optimize", json={"sql": "SELECT 1"}), c.delete("/api/connection")]
print(json.dumps([[r.status_code for r in rs], rs[0].json(), [r.text for r in rs]]))"""


@pytest.mark.parametrize("url", [u for u in MALFORMED if u.startswith("postgresql://")])
def test_p1_malformed_demo_url_is_refused_without_echo(url):
    codes, info, texts = probe(P1_ROUTES, SARGABLE_DEMO_URL=url)
    assert codes == [200, 403, 403, 403, 200]
    assert {k: info[k] for k in ("host", "port", "database", "user", "problems")} == {
        "host": "", "port": "", "database": "", "user": "", "problems": [B4_SENTENCE]}
    assert not [f for t in texts for f in B4_FRAGMENTS if f in t]


# P3: the built frontend is served from SARGABLE_STATIC, behind the API and the Host allow-list.
STATIC = """import json, main
from fastapi.testclient import TestClient
local = TestClient(main.app, base_url="http://localhost", raise_server_exceptions=False)
foreign = TestClient(main.app, base_url="http://evil.example.com", raise_server_exceptions=False)
out = {}
for name, client, path in [("root", local, "/"), ("asset", local, "/assets/app-probe.js"),
                           ("api", local, "/api/connection"), ("unknown_api", local, "/api/no-such-route"),
                           ("foreign_root", foreign, "/"), ("foreign_asset", foreign, "/assets/app-probe.js")]:
    r = client.get(path)
    out[name] = [r.status_code, r.headers.get("content-type", ""), r.text]
print(json.dumps(out))"""
MARKER = "sargable-static-probe-index"
UNREACHABLE_DEMO = "postgresql://postgres:x@127.0.0.1:1/vet?connect_timeout=2"


def test_p3_static_files_are_served_after_the_api_and_behind_the_host_check(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text(f"<!doctype html><title>{MARKER}</title>")
    (tmp_path / "assets" / "app-probe.js").write_text("console.log('sargable-asset-probe')")
    out = probe(STATIC, SARGABLE_STATIC=str(tmp_path), SARGABLE_DEMO_URL=UNREACHABLE_DEMO)
    assert out["root"][0] == 200 and MARKER in out["root"][2]
    assert out["asset"][0] == 200 and "sargable-asset-probe" in out["asset"][2]
    assert out["api"][0] == 200 and out["api"][1].startswith("application/json") and "problems" in json.loads(out["api"][2])
    assert out["unknown_api"][0] == 404 and MARKER not in out["unknown_api"][2]
    for name in ("foreign_root", "foreign_asset"):
        assert out[name][0] == 400 and MARKER not in out[name][2] and "sargable-asset-probe" not in out[name][2]


def test_p3_missing_static_directory_mounts_nothing(tmp_path):
    out = probe(STATIC, SARGABLE_STATIC=str(tmp_path / "missing"), SARGABLE_DEMO_URL=UNREACHABLE_DEMO)
    assert out["root"][0] == 404 and out["asset"][0] == 404
    assert out["api"][0] == 200 and "problems" in json.loads(out["api"][2])


# T3: SARGABLE_LOCAL_HOSTS adds exact, case-insensitive local names.
LOCAL_NAMES = """import json, db
hosts = ["db", "DB", "pg-internal", "xdb", "db.evil.com", "evil.db", "pg-internal.evil.com", "db.example.com"]
print(json.dumps({h: db.tls_problem(f"postgresql://u@{h}/vet?sslmode=disable") is None for h in hosts}))"""


def test_t3_configured_local_hosts_are_exact_and_case_insensitive():
    assert probe(LOCAL_NAMES, SARGABLE_LOCAL_HOSTS="db,,Pg-Internal,") == {
        "db": True, "DB": True, "pg-internal": True, "xdb": False, "db.evil.com": False, "evil.db": False,
        "pg-internal.evil.com": False, "db.example.com": False}
    assert not any(probe(LOCAL_NAMES).values())
