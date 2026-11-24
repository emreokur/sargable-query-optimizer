import threading
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import llm
import main

pytestmark = pytest.mark.integration
client = TestClient(main.app, base_url="http://localhost")
SUPER = "postgresql://postgres:postgres@localhost:5432/vet"
PW = "Canary-PW-90210"
NOHYPO = "sargable_nohypo"
B2 = ["sargable_b2_col", "sargable_b2_trig", "sargable_b2_ref", "sargable_b2_owntab", "sargable_b2_ownschema",
      "sargable_b2_db", "sargable_b2_maint", "sargable_b2_sig"]
ROLES = ["sargable_ro", "sargable_rw", "sargable_wad", "sargable_cdb", "sargable_ind", "sargable_grp", "sargable_cs",
         "sargable_scs", "sargable_su_scs", "sargable_ni_su", "sargable_ni_rw", "sargable_su",
         *B2]
SEQ = "SELECT last_value, is_called FROM sargable_probe_seq"
SQL5 = "SELECT * FROM visits WHERE animal_id = 4242"


def url(role, dbname="vet", password=PW):
    return f"postgresql://{role}:{password}@localhost:5432/{dbname}"


def query(sql, dbname="vet"):
    with psycopg.connect(url("postgres", dbname, "postgres"), autocommit=True) as conn:
        return conn.execute(sql).fetchall()


def cleanup():
    with psycopg.connect(SUPER, autocommit=True) as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {NOHYPO} WITH (FORCE)")
        conn.execute("DROP SEQUENCE IF EXISTS sargable_probe_seq")
        conn.execute("DROP SCHEMA IF EXISTS sargable_probe_schema CASCADE")
        conn.execute("DROP SCHEMA IF EXISTS sargable_b2 CASCADE")
        for role in ROLES:
            if conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,)).fetchone():
                conn.execute(f"DROP OWNED BY {role}")
                conn.execute(f"DROP ROLE {role}")


@pytest.fixture(scope="module", autouse=True)
def roles():
    cleanup()
    with psycopg.connect(SUPER, autocommit=True) as conn:
        for sql in [
            "CREATE SEQUENCE sargable_probe_seq",
            f"CREATE ROLE sargable_ro LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data",
            f"CREATE ROLE sargable_rw LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data",
            "GRANT INSERT, UPDATE, DELETE ON clinics TO sargable_rw",
            "GRANT USAGE, UPDATE ON SEQUENCE sargable_probe_seq TO sargable_rw",
            f"CREATE ROLE sargable_wad LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data, pg_write_all_data",
            f"CREATE ROLE sargable_cdb LOGIN CREATEDB PASSWORD '{PW}' IN ROLE pg_read_all_data",
            "CREATE ROLE sargable_grp NOLOGIN IN ROLE pg_read_server_files",
            f"CREATE ROLE sargable_ind LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data, sargable_grp",
            "CREATE SCHEMA sargable_probe_schema",
            f"CREATE ROLE sargable_cs LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data",
            "GRANT CREATE ON SCHEMA sargable_probe_schema TO sargable_cs",
            f"CREATE ROLE sargable_scs LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data",
            "ALTER ROLE sargable_scs SET standard_conforming_strings = off",
            f"CREATE ROLE sargable_su_scs SUPERUSER LOGIN PASSWORD '{PW}'",
            "ALTER ROLE sargable_su_scs SET standard_conforming_strings = off",
            "CREATE ROLE sargable_su SUPERUSER NOLOGIN",
            f"CREATE ROLE sargable_ni_su LOGIN NOINHERIT PASSWORD '{PW}' IN ROLE sargable_su",
            f"CREATE ROLE sargable_ni_rw LOGIN NOINHERIT PASSWORD '{PW}' IN ROLE sargable_rw",
            "CREATE SCHEMA sargable_b2",
            "CREATE TABLE sargable_b2.t_owned (a int)",
            "CREATE TABLE sargable_b2.pub_t (a int)",
            *[f"CREATE ROLE {r} LOGIN PASSWORD '{PW}' IN ROLE pg_read_all_data" for r in B2],
            "GRANT UPDATE (name) ON clinics TO sargable_b2_col",
            "GRANT TRIGGER ON clinics TO sargable_b2_trig",
            "GRANT REFERENCES ON clinics TO sargable_b2_ref",
            "ALTER TABLE sargable_b2.t_owned OWNER TO sargable_b2_owntab",
            "CREATE SCHEMA sargable_b2_own AUTHORIZATION sargable_b2_ownschema",
            "GRANT CREATE ON DATABASE vet TO sargable_b2_db",
            "GRANT pg_maintain TO sargable_b2_maint",
            "GRANT pg_signal_backend TO sargable_b2_sig",
            f"CREATE DATABASE {NOHYPO}",
        ]:
            conn.execute(sql)
    with psycopg.connect(url("postgres", NOHYPO, "postgres"), autocommit=True) as conn:
        conn.execute("CREATE TABLE t (id int PRIMARY KEY, v int)")
        conn.execute("INSERT INTO t SELECT i, i % 5000 FROM generate_series(1, 200000) i")
        conn.execute("ANALYZE t")
    yield
    cleanup()


@pytest.fixture
def use(monkeypatch):
    def use(role, dbname="vet", password=PW, sandbox=False):
        monkeypatch.setattr(db, "URL", url(role, dbname, password))
        monkeypatch.setattr(db, "SANDBOX", sandbox)
        monkeypatch.setattr(db, "DEMO", False)
    return use


@pytest.fixture
def no_preflight_problems(monkeypatch):
    real = db.preflight
    monkeypatch.setattr(db, "preflight", lambda: {**real(), "problems": []})


def clinics_state():
    return query("SELECT count(*), count(*) FILTER (WHERE name LIKE 'probe%') FROM clinics") + query(SEQ)


def test_s1_s4_read_only_role_preflight_is_clean(use):
    use("sargable_ro")
    info = db.preflight()
    assert info == {"host": "localhost", "port": "5432", "database": "vet", "user": "sargable_ro",
                    "serverVersion": info["serverVersion"], "mode": "live", "demo": False, "hypopg": True, "problems": []}
    assert info["serverVersion"].startswith("17")
    res = client.get("/api/connection")
    assert res.status_code == 200 and res.json()["problems"] == [] and PW not in res.text


def test_s1_sandbox_preflight_lists_no_role_problems(use):
    use("postgres", password="postgres", sandbox=True)
    info = db.preflight()
    assert (info["mode"], info["problems"], info["user"]) == ("sandbox", [], "postgres")


# (exact, minimum) problem counts. A superuser (or a role that can SET ROLE to one) is at least superuser, write and
# schema CREATE; the postgres superuser also has CREATEROLE/CREATEDB/REPLICATION.
@pytest.mark.parametrize("role,password,exact,minimum", [
    ("sargable_rw", PW, 1, 1), ("sargable_cdb", PW, 1, 1), ("sargable_ind", PW, 1, 1), ("sargable_cs", PW, 1, 1),
    ("sargable_ni_rw", PW, 1, 1), ("sargable_ni_su", PW, None, 3),
    ("sargable_b2_col", PW, 1, 1), ("sargable_b2_trig", PW, 1, 1), ("sargable_b2_ref", PW, 1, 1),
    ("sargable_b2_owntab", PW, 1, 1), ("sargable_b2_ownschema", PW, 1, 1), ("sargable_b2_db", PW, 1, 1),
    ("sargable_b2_maint", PW, None, 1), ("sargable_b2_sig", PW, None, 1),
    ("sargable_wad", PW, None, 1), ("postgres", "postgres", None, 4),
])
def test_s4_a4_preflight_reports_each_write_capability(use, role, password, exact, minimum):
    use(role, password=password)
    problems = db.preflight()["problems"]
    assert all(isinstance(p, str) and p and PW not in p for p in problems)
    assert len(problems) >= minimum
    if exact is not None:
        assert len(problems) == exact


@pytest.mark.parametrize("role,password", [
    ("sargable_rw", PW), ("sargable_wad", PW), ("sargable_cdb", PW), ("sargable_ind", PW), ("sargable_cs", PW),
    ("sargable_ni_rw", PW), ("sargable_ni_su", PW), ("postgres", "postgres"),
    *[(r, PW) for r in B2]])
def test_s4_live_routes_refuse_with_403_and_execute_nothing(use, monkeypatch, role, password):
    use(role, password=password)
    monkeypatch.setattr(llm, "propose", lambda *a: pytest.fail("Claude must not be called"))
    before = clinics_state()
    for path in ("/api/explain", "/api/optimize"):
        res = client.post(path, json={"sql": "SELECT nextval('sargable_probe_seq')"})
        assert res.status_code == 403 and res.json()["detail"] and PW not in res.text
    assert clinics_state() == before


WRITES = [
    "DELETE FROM clinics",
    "UPDATE clinics SET name = 'probe-name'",
    "INSERT INTO clinics VALUES (999999, 'probe-x', 'y')",
    "WITH d AS (DELETE FROM clinics RETURNING 1) SELECT count(*) FROM d",
    "SELECT nextval('sargable_probe_seq')",
    "SELECT * INTO sargable_probe_t FROM clinics",
]


# Not run as a superuser: PostgreSQL 17 lets a superuser run EXPLAIN ANALYZE SELECT INTO inside a READ ONLY
# transaction (only the rollback undoes it), so the role and the parser are the layers that stop that one.
@pytest.mark.parametrize("role,password,trust_role", [("sargable_ro", PW, False), ("sargable_rw", PW, True)])
def test_s6_database_refuses_writes_even_without_the_parser(use, monkeypatch, request, role, password, trust_role):
    use(role, password=password)
    monkeypatch.setattr(db, "check_select", lambda sql: None)
    if trust_role:
        request.getfixturevalue("no_preflight_problems")
    before = clinics_state()
    for sql in WRITES:
        with pytest.raises(psycopg.Error):
            db.explain(sql)
        assert db.verify_fix(sql, [], 200, 1.0).keys() == {"skipped", "testable"}
    try:
        db.explain("SELECT set_config('transaction_read_only', 'off', true)")
    except psycopg.Error:
        pass
    with pytest.raises(psycopg.Error):
        db.explain("DELETE FROM clinics")
    assert clinics_state() == before
    assert query("SELECT 1 FROM pg_class WHERE relname = 'sargable_probe_t'") == []


def watch_while(fn, sql_filter):
    seen, result, done = [], {}, threading.Event()

    def run():
        try:
            result["value"] = fn()
        finally:
            done.set()

    thread = threading.Thread(target=run)
    with psycopg.connect(SUPER, autocommit=True) as conn:
        thread.start()
        while True:
            seen.append(conn.execute(sql_filter).fetchone()[0])
            if done.is_set():
                break
            time.sleep(0.005)
    thread.join()
    return result["value"], seen


BUILDS = """SELECT (SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND query ~* '^\\s*create\\s')
  + (SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
     WHERE c.relname = 'visits' AND l.mode IN ('ShareLock', 'ShareRowExclusiveLock', 'AccessExclusiveLock'))
  + (SELECT count(*) FROM pg_indexes WHERE indexname = 'sargable_probe_idx')"""


@pytest.mark.parametrize("role,password,trust_role", [("sargable_ro", PW, False), ("postgres", "postgres", True)])
def test_s7_live_index_fix_is_estimated_with_hypopg_and_never_built(use, request, role, password, trust_role):
    use(role, password=password)
    if trust_role:
        request.getfixturevalue("no_preflight_problems")
    before = db.explain(SQL5)
    ddl = ["CREATE INDEX sargable_probe_idx ON visits (animal_id)"]
    result, seen = watch_while(lambda: db.verify_fix(SQL5, ddl, before["plan"]["rows"], before["planCost"]), BUILDS)
    assert seen and max(seen) == 0
    assert result.keys() == {"estimate"}
    est = result["estimate"]
    assert est["beforeCost"] == before["planCost"] and est["afterCost"] < est["beforeCost"] and est["usesIndex"] is True
    assert query("SELECT 1 FROM pg_indexes WHERE indexname = 'sargable_probe_idx'") == []


def test_s7_live_index_fix_is_skipped_without_hypopg(use):
    use("sargable_ro", NOHYPO)
    assert db.preflight()["hypopg"] is False
    before = db.explain("SELECT * FROM t WHERE v = 5")
    result = db.verify_fix("SELECT * FROM t WHERE v = 5", ["CREATE INDEX sargable_probe_idx ON t (v)"],
                           before["plan"]["rows"], before["planCost"])
    assert result.keys() == {"skipped"} and isinstance(result["skipped"], str) and result["skipped"]
    assert query("SELECT indexname FROM pg_indexes WHERE tablename = 't'", NOHYPO) == [("t_pkey",)]


@pytest.mark.parametrize("ddl", [
    ["DROP INDEX visits_pkey"],
    ["CREATE INDEX CONCURRENTLY sargable_probe_idx ON visits (animal_id)"],
    ["CREATE INDEX sargable_probe_idx ON visits (animal_id)", "DROP TABLE clinics"],
])
def test_s7_live_rejected_ddl_is_an_error(use, ddl):
    use("sargable_ro")
    assert db.verify_fix(SQL5, ddl, 7, 100.0).keys() == {"error"}
    assert query("SELECT indexname FROM pg_indexes WHERE tablename = 'visits'") == [("visits_pkey",)]
    assert query("SELECT count(*) FROM clinics") == [(200,)]


def test_b1_live_rewrites_are_never_run_automatically_and_non_selects_are_errors(use):
    use("sargable_ro")
    before = db.explain("SELECT * FROM clinics")
    args = (before["plan"]["rows"], before["planCost"])
    for sql in ("SELECT * FROM clinics ORDER BY id", "SELECT * FROM clinics WHERE id <= 10"):
        result = db.verify_fix(sql, [], *args)
        assert result.keys() == {"skipped", "testable"} and result["testable"] is True and result["skipped"]
    state = clinics_state()
    for sql in ("DELETE FROM clinics", "SELECT * INTO sargable_probe_t FROM clinics", "SELECT * FROM clinics FOR UPDATE"):
        assert db.verify_fix(sql, [], *args).keys() == {"error"}
    assert clinics_state() == state


def test_b1_exploit_rewrite_through_live_optimize_does_not_reach_the_database(use, monkeypatch):
    use("sargable_ro")
    with psycopg.connect(url("sargable_ro"), autocommit=True) as victim:
        pid = victim.info.backend_pid
        exploit = f"SELECT count(*) FROM clinics WHERE (SELECT (s.x).pg_terminate_backend FROM (SELECT {pid} AS x) s)"
        monkeypatch.setattr(llm, "propose", lambda sql, explain, schema, live: llm.Proposal(diagnosis="d", fixes=[
            llm.ProposedFix(title="Rewrite", kind="rewrite", rationale="r", ddl=[], rewritten_sql=exploit)]))
        res = client.post("/api/optimize", json={"sql": "SELECT count(*) FROM clinics"})
        assert res.status_code == 200
        [fix] = res.json()["fixes"]
        assert fix["sql"] == exploit and "afterMs" not in fix and "plan" not in fix
        assert fix.get("testable") is True or "error" in fix
        assert victim.execute("SELECT 1").fetchone() == (1,)
        assert query(f"SELECT count(*) FROM pg_stat_activity WHERE pid = {pid}") == [(1,)]


def test_s1_unreachable_host_reports_cannot_connect_without_password(monkeypatch):
    monkeypatch.setattr(db, "URL", f"postgresql://app:{PW}@nonexistent.invalid:5432/prod?connect_timeout=2")
    monkeypatch.setattr(db, "SANDBOX", False)
    monkeypatch.setattr(db, "DEMO", False)
    info = db.preflight()
    assert info["mode"] == "live" and info["serverVersion"] is None and info["hypopg"] is False
    assert any(p.startswith("Cannot connect") for p in info["problems"])
    assert PW not in str(info)
    res = client.post("/api/explain", json={"sql": "SELECT 1"})
    assert res.status_code == 403 and PW not in res.text


SLEEPERS = "SELECT count(*) FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND state = 'active' AND query LIKE '%pg_sleep(0.6)%'"


@pytest.mark.parametrize("role,password,sandbox", [("postgres", "postgres", True), ("sargable_ro", PW, False)])
def test_s13_one_statement_at_a_time(use, role, password, sandbox):
    use(role, password=password, sandbox=sandbox)
    sql = "SELECT pg_sleep(0.6)"
    middle = (lambda: db.verify_fix(sql, [], 1, 1.0)) if sandbox else (lambda: db.explain(sql))
    jobs = [lambda: db.explain(sql), middle, lambda: db.explain(sql)]
    results = [None] * len(jobs)

    def both():
        threads = [threading.Thread(target=lambda i=i: results.__setitem__(i, jobs[i]())) for i in range(len(jobs))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    start = time.monotonic()
    _, seen = watch_while(both, SLEEPERS)
    assert time.monotonic() - start >= 1.7
    assert max(seen) == 1
    assert all(r is not None and "error" not in r for r in results)


CRAFTED = r"WITH d AS (SELECT 'a\' || ' ), e AS (DELETE FROM clinics RETURNING 1) SELECT 1 FROM d --' ) SELECT 1 FROM d"


def plan_types(node):
    return [node["type"]] + [t for c in node["children"] for t in plan_types(c)]


@pytest.mark.parametrize("role,sandbox", [("sargable_scs", False), ("sargable_su_scs", True)])
def test_a1_strings_are_lexed_with_standard_conforming_strings_on(use, role, sandbox):
    use(role, sandbox=sandbox)
    assert query(f"SELECT setconfig FROM pg_db_role_setting s JOIN pg_roles r ON r.oid = s.setrole WHERE rolname = '{role}'") \
        == [(["standard_conforming_strings=off"],)]
    assert db.check_select(CRAFTED) is None
    assert plan_types(db.explain(r"SELECT 'a\'")["plan"]) == ["Result"]
    before = clinics_state()
    assert "ModifyTable" not in plan_types(db.explain(CRAFTED)["plan"])
    assert clinics_state() == before


def test_b2_read_only_roles_report_nothing_and_a_public_grant_is_reported(use):
    use("demo_readonly", password="demo_readonly")
    assert db.preflight()["problems"] == []
    use("sargable_ro")
    assert db.preflight()["problems"] == []
    with psycopg.connect(SUPER, autocommit=True) as conn:
        conn.execute("GRANT INSERT ON sargable_b2.pub_t TO PUBLIC")
        try:
            assert len(db.preflight()["problems"]) == 1
            res = client.post("/api/explain", json={"sql": "SELECT 1"})
            assert res.status_code == 403 and PW not in res.text
        finally:
            conn.execute("REVOKE INSERT ON sargable_b2.pub_t FROM PUBLIC")
    assert db.preflight()["problems"] == []
