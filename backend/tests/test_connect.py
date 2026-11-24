import json
import os
import socket
import ssl
import struct
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

import db
import llm
import main

client = TestClient(main.app, base_url="http://localhost", raise_server_exceptions=False)
CANARY = "Cn4ry-PW-v4-77"
FRAGMENTS = [CANARY, "Cn4ry", "PW-v4", "v4-77", "4ry-PW"]
GOOD = {"host": "h0st-canary.example.com", "port": 5432, "database": "db-canary-77", "user": "us3r-canary",
        "password": CANARY, "sslmode": "require", "sandbox": False, "ca_cert": ""}
UNREACHABLE = {**GOOD, "host": "127.0.0.1", "port": 1, "sslmode": "disable"}
IMPORT_STATE = (os.environ.get("DATABASE_URL",
                               os.environ.get("SARGABLE_DEMO_URL", "postgresql://postgres:postgres@localhost:5432/vet")),
                "DATABASE_URL" not in os.environ,
                "DATABASE_URL" not in os.environ or os.environ.get("SARGABLE_SANDBOX") == "1")


def state():
    return db.URL, db.DEMO, db.SANDBOX


def leaks(text):
    return [f for f in FRAGMENTS if f in text]


@pytest.fixture(autouse=True)
def restore_connection():
    saved = state()
    yield
    try:
        db.reset()
    finally:
        db.URL, db.DEMO, db.SANDBOX = saved


def configure(**changes):
    db.configure(**{**GOOD, **changes})


# C1: every invalid field raises ValueError naming the field, echoing nothing, changing nothing.
NUL_PW = CANARY[:5] + "\x00" + CANARY[5:]
INVALID = [
    ("host", ""), ("host", "   "), ("host", None), ("host", 5), ("host", ["evil-host"]), ("host", "app@evil.example.com"), ("host", "@"), ("host", "h0st\x00evil"),
    ("database", ""), ("database", " \t"), ("database", None), ("database", ["evil-db"]), ("database", "db-canary\x00"),
    ("user", ""), ("user", "  "), ("user", None), ("user", 7), ("user", {"evil": 1}), ("user", "us3r\x00x"),
    ("password", None), ("password", 123), ("password", CANARY.encode()), ("password", NUL_PW),
    ("port", "5432"), ("port", 5432.0), ("port", True), ("port", False), ("port", 0), ("port", 65536), ("port", -1),
    ("port", None),
    ("sslmode", "prefer"), ("sslmode", "allow"), ("sslmode", "verify-ca"), ("sslmode", "REQUIRE"), ("sslmode", ""),
    ("sslmode", None), ("sslmode", "require\x00"),
    ("sandbox", "true"), ("sandbox", 1), ("sandbox", 0), ("sandbox", None), ("sandbox", "false"),
    ("ca_cert", None), ("ca_cert", 5), ("ca_cert", ["evil-ca"]),
]


@pytest.mark.parametrize("field,value", INVALID)
def test_c1_invalid_field_is_rejected_without_echo_or_change(field, value):
    configure(password="first-pw-zz", sandbox=True)
    before = state()
    with pytest.raises(ValueError) as e:
        configure(**{field: value})
    message = str(e.value)
    assert field in message
    assert not leaks(message)
    assert not [v for v in ("h0st-canary", "db-canary", "us3r-canary", "first-pw-zz", "evil") if v in message]
    assert state() == before


@pytest.mark.parametrize("port", [1, 5432, 65535])
def test_c1_valid_edge_values_are_accepted(port):
    configure(port=port, password="")
    assert conninfo_to_dict(db.URL)["port"] == str(port)


# C2: values round-trip exactly and cannot inject other keys.
@pytest.mark.parametrize("field,key,value", [
    ("password", "password", "p' sslmode='disable"),
    ("password", "password", "  spaced both ends  "),
    ("password", "password", "back\\slash \\' \\\\"),
    ("password", "password", "@%=; q'uote \"dq\""),
    ("password", "password", "postgresql://evil:x@evil.example.com:5432/db?sslmode=disable&host=evil"),
    ("password", "password", ""),
    ("password", "password", CANARY),
    ("host", "host", "x sslmode=disable"),
    ("host", "host", "h' sslmode='disable"),
    ("user", "user", "u password=p"),
    ("user", "user", "o'neil\\"),
    ("database", "dbname", "db' host='evil"),
    ("database", "dbname", "my db=x;%@"),
])
def test_c2_conninfo_round_trips_without_injection(field, key, value):
    configure(**{field: value})
    expected = {"host": GOOD["host"], "port": "5432", "dbname": GOOD["database"], "user": GOOD["user"],
                "password": CANARY, "sslmode": "require"}
    expected[key] = value
    assert conninfo_to_dict(db.URL) == expected


def test_c2_verify_full_adds_system_root_certificates():
    configure(sslmode="verify-full")
    info = conninfo_to_dict(db.URL)
    assert info["sslmode"] == "verify-full" and info["sslrootcert"] == "system"
    assert set(info) == {"host", "port", "dbname", "user", "password", "sslmode", "sslrootcert"}


# C3: mode and reset.
@pytest.mark.parametrize("sandbox", [False, True])
def test_c3_configure_sets_mode_and_reset_restores_import_state(monkeypatch, sandbox):
    configure(sandbox=sandbox)
    assert (db.DEMO, db.SANDBOX) == (False, sandbox)
    db.reset()
    assert state() == IMPORT_STATE


def test_c3_reset_restores_environment_startup_connection_not_the_demo():
    from test_config import probe
    remote = "postgresql://app:x@db.example.com/prod?sslmode=require"
    code = ("import json, db; db.configure('h', 5432, 'd', 'u', 'p', 'require', True, ''); a = [db.URL, db.DEMO, db.SANDBOX];"
            " db.reset(); print(json.dumps([a[1:], [db.URL, db.DEMO, db.SANDBOX]]))")
    assert probe(code, DATABASE_URL=remote) == [[False, True], [remote, False, False]]
    assert probe(code, DATABASE_URL=remote, SARGABLE_SANDBOX="1") == [[False, True], [remote, False, True]]


def test_c8_every_connection_uses_a_five_second_timeout(monkeypatch):
    seen = []

    def record(*args, **kwargs):
        seen.append(kwargs)
        raise psycopg.OperationalError("refused")

    monkeypatch.setattr(psycopg, "connect", record)
    monkeypatch.setattr(psycopg.Connection, "connect", record)
    configure(**{**UNREACHABLE, "sandbox": True})
    db.preflight()
    with pytest.raises(psycopg.Error):
        db.explain("SELECT 1")
    db.verify_fix("SELECT 1", ["CREATE INDEX ON t (a)"], 1, 1.0)
    with pytest.raises(psycopg.Error):
        db.schema()
    assert len(seen) == 4 and all(str(k.get("connect_timeout")) == "5" for k in seen)


# C4, C5, C7: routes.
def test_c4_c5_post_returns_preflight_for_the_new_connection_without_the_password():
    res = client.post("/api/connection", json=UNREACHABLE)
    assert res.status_code == 200
    body = res.json()
    assert {k: body[k] for k in ("host", "port", "database", "user", "mode", "demo", "serverVersion")} == {
        "host": "127.0.0.1", "port": "1", "database": GOOD["database"], "user": GOOD["user"], "mode": "live",
        "demo": False, "serverVersion": None}
    assert len(body["problems"]) == 1 and body["problems"][0].startswith("Cannot connect")
    assert client.get("/api/connection").json() == body
    sandbox = client.post("/api/connection", json={**UNREACHABLE, "sandbox": True}).json()
    assert sandbox["mode"] == "sandbox" and sandbox["demo"] is False
    for res in [res, client.get("/api/connection"), client.get("/api/schema"),
                client.post("/api/explain", json={"sql": "SELECT 1"}), client.post("/api/optimize", json={"sql": "SELECT 1"})]:
        assert not leaks(res.text)


def test_c4_delete_resets_and_returns_preflight_for_the_defaults(monkeypatch):
    configure()
    monkeypatch.setattr(db, "preflight", lambda: {"url": db.URL, "demo": db.DEMO, "sandbox": db.SANDBOX})
    res = client.delete("/api/connection")
    assert res.status_code == 200
    assert res.json() == {"url": IMPORT_STATE[0], "demo": IMPORT_STATE[1], "sandbox": IMPORT_STATE[2]}
    assert state() == IMPORT_STATE


BAD_BODIES = [
    *[{k: v for k, v in UNREACHABLE.items() if k != missing} for missing in UNREACHABLE],
    {**UNREACHABLE, "extra": "x"},
    {**UNREACHABLE, "dsn": f"postgresql://u:{CANARY}@evil/db"},
    {**UNREACHABLE, "port": "5432"},
    {**UNREACHABLE, "port": True},
    {**UNREACHABLE, "sandbox": "yes"},
    {**UNREACHABLE, "password": 12345},
    {**UNREACHABLE, "password": [CANARY]},
    {**UNREACHABLE, "password": {"value": CANARY}},
    {**UNREACHABLE, "password": NUL_PW},
    {**UNREACHABLE, "sslmode": "prefer"},
    {**UNREACHABLE, "host": ""},
    {**UNREACHABLE, "host": f"{CANARY}@127.0.0.1"},
    [UNREACHABLE],
    CANARY,
    None,
    {},
]


@pytest.mark.parametrize("body", BAD_BODIES)
def test_c5_invalid_body_is_400_without_any_submitted_value(body):
    configure(password="first-pw-zz")
    before = state()
    res = client.post("/api/connection", json=body)
    assert res.status_code == 400
    assert isinstance(res.json()["detail"], str) and res.json()["detail"]
    assert not leaks(res.text) and "127.0.0.1" not in res.text and "first-pw-zz" not in res.text
    assert state() == before


def test_c5_malformed_json_is_rejected_without_echo():
    before = state()
    res = client.post("/api/connection", content=f'{{"password": "{CANARY}", "host": ',
                      headers={"Content-Type": "application/json"})
    assert 400 <= res.status_code < 500 and not leaks(res.text)
    assert state() == before


def test_c7_foreign_host_and_non_json_cannot_change_the_connection():
    before = state()
    foreign = TestClient(main.app, base_url="http://evil.example.com")
    assert foreign.post("/api/connection", json=UNREACHABLE).status_code == 400
    assert foreign.delete("/api/connection").status_code == 400
    res = client.post("/api/connection", content=json.dumps(UNREACHABLE), headers={"Content-Type": "text/plain"})
    assert 400 <= res.status_code < 500 and not leaks(res.text)
    assert state() == before


# C6: nothing reaches the disk.
def test_c6_connecting_writes_no_file(tmp_path, monkeypatch):
    work, home = tmp_path / "cwd", tmp_path / "home"
    work.mkdir()
    home.mkdir()
    monkeypatch.chdir(work)
    monkeypatch.setenv("HOME", str(home))
    configure()
    client.post("/api/connection", json=UNREACHABLE)
    client.get("/api/connection")
    client.post("/api/explain", json={"sql": "SELECT 1"})
    client.post("/api/connection", json={**UNREACHABLE, "port": "bad"})
    client.delete("/api/connection")
    written = [p for d in (work, home) for p in Path(d).rglob("*") if p.is_file()]
    assert [p for p in written if CANARY.encode() in p.read_bytes()] == []


# C8 and C9: against the demo container.
DEMO_RO = {"host": "localhost", "port": 5432, "database": "vet", "user": "demo_readonly", "password": "demo_readonly",
           "sslmode": "disable", "sandbox": False, "ca_cert": ""}
SUPER = {**DEMO_RO, "user": "postgres", "password": "postgres"}
SQL5 = "SELECT * FROM visits WHERE animal_id = 4242"


def propose_index(name):
    return lambda sql, explain, schema, live: llm.Proposal(diagnosis="d", fixes=[llm.ProposedFix(
        title="Index", kind="index", rationale="r", ddl=[f"CREATE INDEX {name} ON visits (animal_id)"], rewritten_sql=None)])


def visits_indexes():
    with psycopg.connect("postgresql://postgres:postgres@localhost:5432/vet") as conn:
        return conn.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'visits'").fetchall()


@pytest.mark.integration
def test_c8_read_only_role_connected_from_the_app_is_live_and_clean(monkeypatch):
    res = client.post("/api/connection", json=DEMO_RO)
    assert res.status_code == 200
    info = res.json()
    assert (info["mode"], info["demo"], info["user"], info["problems"], info["hypopg"]) == (
        "live", False, "demo_readonly", [], True)
    assert client.post("/api/explain", json={"sql": "DELETE FROM visits"}).status_code == 400
    monkeypatch.setattr(llm, "propose", propose_index("sargable_c8_idx"))
    res = client.post("/api/optimize", json={"sql": SQL5})
    assert res.status_code == 200
    [fix] = res.json()["fixes"]
    assert "estimate" in fix and "afterMs" not in fix
    assert visits_indexes() == [("visits_pkey",)]


@pytest.mark.integration
def test_c8_superuser_connected_live_is_refused():
    info = client.post("/api/connection", json=SUPER).json()
    assert info["mode"] == "live" and len(info["problems"]) >= 1
    res = client.post("/api/explain", json={"sql": "SELECT 1"})
    assert res.status_code == 403


@pytest.mark.integration
def test_c8_superuser_with_sandbox_builds_and_times_indexes_then_rolls_back(monkeypatch):
    info = client.post("/api/connection", json={**SUPER, "sandbox": True}).json()
    assert (info["mode"], info["demo"], info["problems"]) == ("sandbox", False, [])
    monkeypatch.setattr(llm, "propose", propose_index("sargable_c8_idx"))
    [fix] = client.post("/api/optimize", json={"sql": SQL5}).json()["fixes"]
    assert fix.keys() >= {"afterMs", "plan", "rowsMatch"}
    assert visits_indexes() == [("visits_pkey",)]


@pytest.mark.integration
def test_c8_unreachable_host_reports_cannot_connect_quickly():
    start = time.monotonic()
    info = client.post("/api/connection", json={**DEMO_RO, "host": "127.0.0.1", "port": 1}).json()
    assert time.monotonic() - start < 6
    assert info["problems"] and info["problems"][0].startswith("Cannot connect")


@pytest.mark.integration
def test_c9_configure_waits_for_a_running_statement():
    configure(**{**SUPER, "sandbox": True})
    done = {}

    def run():
        done["result"] = db.explain("SELECT pg_sleep(0.8)")
        done["at"] = time.monotonic()

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(0.3)
    configure(**UNREACHABLE)
    returned = time.monotonic()
    thread.join()
    assert "plan" in done["result"]
    assert returned >= done["at"]
    assert conninfo_to_dict(db.URL)["port"] == "1"


# D1: explain and verify_fix run entirely on the connection current when they started; a switch waits for them.
@pytest.mark.integration
@pytest.mark.parametrize("operation", ["explain", "verify_fix"])
@pytest.mark.parametrize("switch", ["configure", "reset"])
def test_d1_connection_switch_waits_for_the_whole_operation(monkeypatch, operation, switch):
    configure(**DEMO_RO)
    real_preflight = db.preflight
    events = {}
    switched = threading.Event()

    def do_switch():
        if switch == "configure":
            configure(**{**SUPER, "sandbox": True})
        else:
            db.reset()
        events["switched_at"] = time.monotonic()
        switched.set()

    def preflight_then_switch():
        info = real_preflight()
        if "thread" not in events:
            events["thread"] = threading.Thread(target=do_switch)
            events["thread"].start()
            events["switched_during_operation"] = switched.wait(0.4)
        return info

    monkeypatch.setattr(db, "preflight", preflight_then_switch)
    if operation == "explain":
        result = db.explain(SQL5)
        assert "plan" in result
    else:
        result = db.verify_fix(SQL5, ["CREATE INDEX ON visits (animal_id)"], 7, 1e9)
        assert result.keys() == {"estimate"}
    finished_at = time.monotonic()
    assert "thread" in events, "preflight was not called inside the operation"
    assert events["switched_during_operation"] is False
    assert switched.wait(1.0), "the switch never completed after the operation"
    events["thread"].join(1.0)
    assert events["switched_at"] >= finished_at
    if switch == "configure":
        assert (db.DEMO, db.SANDBOX, conninfo_to_dict(db.URL)["user"]) == (False, True, "postgres")
    else:
        assert state() == IMPORT_STATE
    assert visits_indexes() == [("visits_pkey",)]



# T1, T2: verified TLS for remote hosts, and a CA certificate written to a private temporary file.
@pytest.fixture(scope="module")
def certs(tmp_path_factory):
    out = {}
    for name in ("server", "other"):
        d = tmp_path_factory.mktemp(name)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(d / "key.pem"),
                        "-out", str(d / "cert.pem"), "-days", "2", "-subj", f"/CN=localhost-{name}",
                        "-addext", "subjectAltName=DNS:localhost"], check=True, capture_output=True)
        out[name] = {"cert": (d / "cert.pem").read_text(), "key": (d / "key.pem").read_text(), "dir": d}
    return out


def body_of(pem):
    return pem.splitlines()[5]


@pytest.fixture
def mkstemp_spy(monkeypatch, tmp_path):
    made = []
    real = tempfile.mkstemp

    def spy(*args, **kwargs):
        made.append(real(*args, **kwargs))
        return made[-1]

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(tempfile, "mkstemp", spy)
    return made


def test_t2_ca_certificate_goes_to_a_private_file_that_the_next_change_deletes(certs, mkstemp_spy):
    cert = certs["server"]["cert"]
    configure(sslmode="verify-full", ca_cert=cert)
    path = conninfo_to_dict(db.URL)["sslrootcert"]
    assert Path(path).read_text() == cert and os.stat(path).st_mode & 0o777 == 0o600
    assert len(mkstemp_spy) == 1
    configure(sslmode="verify-full")
    assert conninfo_to_dict(db.URL)["sslrootcert"] == "system" and not Path(path).exists()
    configure(sslmode="verify-full", ca_cert=cert)
    second = conninfo_to_dict(db.URL)["sslrootcert"]
    db.reset()
    assert not Path(second).exists() and state() == IMPORT_STATE


@pytest.mark.parametrize("bad", ["with_require", "not_pem", "private_key", "too_big", "nul"])
def test_t2_invalid_ca_certificate_is_rejected_without_writing_deleting_or_echoing(certs, mkstemp_spy, tmp_path, bad):
    cert = certs["server"]["cert"]
    configure(sslmode="verify-full", ca_cert=cert)
    before, kept, files = state(), conninfo_to_dict(db.URL)["sslrootcert"], sorted(tmp_path.iterdir())
    changes = {"with_require": {"sslmode": "require", "ca_cert": cert},
               "not_pem": {"sslmode": "verify-full", "ca_cert": "evil-not-a-certificate"},
               "private_key": {"sslmode": "verify-full", "ca_cert": certs["server"]["key"]},
               "too_big": {"sslmode": "verify-full", "ca_cert": cert * (65536 // len(cert) + 1)},
               "nul": {"sslmode": "verify-full", "ca_cert": cert + "\x00"}}[bad]
    with pytest.raises(ValueError) as e:
        configure(**changes)
    assert "ca_cert" in str(e.value)
    for secret in (body_of(cert), body_of(certs["server"]["key"]), "evil-not", CANARY):
        assert secret not in str(e.value)
    assert state() == before and Path(kept).exists() and sorted(tmp_path.iterdir()) == files and len(mkstemp_spy) == 1
    res = client.post("/api/connection", json={**UNREACHABLE, **changes})
    assert res.status_code == 400 and body_of(cert) not in res.text and body_of(certs["server"]["key"]) not in res.text
    assert state() == before and len(mkstemp_spy) == 1


def test_t2_route_accepts_a_certificate_and_never_returns_it(certs):
    res = client.post("/api/connection", json={**UNREACHABLE, "sslmode": "verify-full", "ca_cert": certs["server"]["cert"]})
    assert res.status_code == 200 and body_of(certs["server"]["cert"]) not in res.text and not leaks(res.text)
    assert body_of(certs["server"]["cert"]) not in client.get("/api/connection").text


@pytest.mark.parametrize("sslmode,refused", [("require", True), ("verify-full", False)])
def test_t1_remote_host_needs_verified_tls(monkeypatch, sslmode, refused):
    def unreachable(*args, **kwargs):
        raise psycopg.OperationalError("refused")

    monkeypatch.setattr(psycopg, "connect", unreachable)
    monkeypatch.setattr(psycopg.Connection, "connect", unreachable)
    configure(host="db.example.com", sslmode=sslmode)
    problems = db.preflight()["problems"]
    tls = [p for p in problems if not p.startswith("Cannot connect")]
    assert bool(tls) is refused
    if refused:
        assert "verify-full" in tls[0]
        assert client.post("/api/explain", json={"sql": "SELECT 1"}).status_code == 403


def fake_postgres(cert_dir, captured):
    """A TLS 'PostgreSQL server' that asks for a cleartext password and records what it receives."""
    listener = socket.create_server(("127.0.0.1", 0))
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_dir / "cert.pem", cert_dir / "key.pem")

    def serve():
        listener.settimeout(10)
        try:
            conn, _ = listener.accept()
        except OSError:
            return
        with conn:
            while struct.unpack("!ii", conn.recv(8))[1] == 80877104:
                conn.sendall(b"N")
            conn.sendall(b"S")
            try:
                tls = context.wrap_socket(conn, server_side=True)
                tls.recv(4096)
                tls.sendall(b"R" + struct.pack("!ii", 8, 3))
                captured.append(tls.recv(4096))
            except (ssl.SSLError, OSError):
                captured.append(b"")

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    return listener.getsockname()[1], thread, listener


@pytest.mark.parametrize("ca,sent", [("other", False), ("server", True)])
def test_t2_verify_full_with_the_wrong_ca_never_sends_the_password(certs, ca, sent):
    captured = []
    port, thread, listener = fake_postgres(certs["server"]["dir"], captured)
    with listener:
        configure(host="localhost", port=port, sslmode="verify-full", ca_cert=certs[ca]["cert"])
        assert db.preflight()["problems"][0].startswith("Cannot connect")
        thread.join(10)
    assert (CANARY.encode() in b"".join(captured)) is sent
