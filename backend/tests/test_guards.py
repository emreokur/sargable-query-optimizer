import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import llm
import main

client = TestClient(main.app, base_url="http://localhost")
BAD_SQL = ["", "   \n", ";", " ; ", "SELECT 1; SELECT 2", "SELECT 1;;", "DELETE FROM clinics; SELECT 1"]


@pytest.fixture(autouse=True)
def no_database(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("guard must run before any database access")

    monkeypatch.setattr(psycopg, "connect", refuse)
    monkeypatch.setattr(psycopg.Connection, "connect", refuse)


@pytest.fixture(autouse=True)
def no_libpq_environment(monkeypatch):
    for var in ("PGHOST", "PGHOSTADDR", "PGSSLMODE"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture(params=["sandbox", "live"])
def mode(request, monkeypatch):
    monkeypatch.setattr(db, "SANDBOX", request.param == "sandbox")
    monkeypatch.setattr(db, "DEMO", False)
    return request.param


@pytest.fixture
def live(monkeypatch):
    monkeypatch.setattr(db, "SANDBOX", False)
    monkeypatch.setattr(db, "DEMO", False)


@pytest.mark.parametrize("sql", BAD_SQL)
def test_f2_explain_guard_raises_value_error(mode, sql):
    with pytest.raises(ValueError):
        db.explain(sql)


@pytest.mark.parametrize("path", ["/api/explain", "/api/optimize"])
@pytest.mark.parametrize("sql", ["", "SELECT 1; SELECT 2"])
def test_f2_routes_reject_empty_and_multi_statement(mode, path, sql):
    res = client.post(path, json={"sql": sql})
    assert res.status_code == 400
    assert isinstance(res.json()["detail"], str) and res.json()["detail"]


@pytest.mark.parametrize("stmt", [
    "CREATE INDEX ON visits (animal_id)",
    "create index probe ON visits (animal_id);",
    "  Create Unique Index probe ON owners (lower(email)) ; \n",
    "CREATE INDEX probe ON visits (visited_at) WHERE status = 'open'",
])
def test_f4_check_ddl_accepts_create_index(stmt):
    assert db.check_ddl(stmt) is None


@pytest.mark.parametrize("stmt", [
    "",
    "DROP INDEX visits_pkey",
    "DROP TABLE visits",
    "SELECT 1",
    "CREATE TABLE probe (a int)",
    "ALTER TABLE visits ADD COLUMN x int",
    "CREATE INDEX CONCURRENTLY probe ON visits (animal_id)",
    "create unique index concurrently probe ON visits (animal_id)",
    "CREATE INDEX probe ON visits (animal_id); DROP TABLE visits",
    "CREATE INDEX probe ON visits (animal_id);;",
    "-- CREATE INDEX\nDROP TABLE visits",
])
def test_f4_check_ddl_rejects_everything_else(stmt):
    with pytest.raises(ValueError):
        db.check_ddl(stmt)


SELECTS = [
    "SELECT 1",
    "select * from visits where id = 1",
    "SELECT 1;",
    "WITH x AS (SELECT id FROM visits) SELECT * FROM x",
    "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r WHERE n < 5) SELECT n FROM r",
    "TABLE clinics",
    "VALUES (1, 'a'), (2, 'b')",
    "SELECT * FROM (SELECT id FROM visits) s WHERE s.id IN (SELECT animal_id FROM visits)",
    "SELECT id, row_number() OVER (PARTITION BY animal_id ORDER BY visited_at) FROM visits",
    "SELECT 1 UNION SELECT 2 EXCEPT SELECT 3",
    "SELECT 'delete from t; update t set a = 1; insert into t' AS note",
    "SELECT * FROM visits WHERE reason = 'for update' AND status <> 'into'",
    'SELECT "delete", "update", "into" FROM t',
    "SELECT 1 -- ; DELETE FROM visits",
    "SELECT 1 /* ; DROP TABLE visits */",
    "SELECT pg_sleep(0), now()",
    "SELECT * FROM visits v WHERE EXISTS (SELECT 1 FROM animals a WHERE a.id = v.animal_id)",
]

NOT_SELECTS = [
    "", "   ", "-- nothing", "SELEC 1",
    "SELECT 1; SELECT 2", "SELECT 1; DELETE FROM visits",
    "INSERT INTO t VALUES (1)", "INSERT INTO t SELECT * FROM u", "UPDATE t SET a = 1", "DELETE FROM t",
    "MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE", "TRUNCATE t",
    "DROP TABLE t", "CREATE TABLE t (a int)", "CREATE INDEX ON t (a)", "ALTER TABLE t ADD c int",
    "GRANT SELECT ON t TO public", "CREATE TABLE x AS SELECT 1", "CREATE VIEW v AS SELECT 1",
    "SELECT * INTO new_t FROM t", "SELECT 1 INTO TEMP x", "SELECT * INTO new_t FROM t UNION SELECT * FROM u",
    "SELECT * FROM t FOR UPDATE", "SELECT * FROM t FOR SHARE", "SELECT * FROM t FOR NO KEY UPDATE SKIP LOCKED",
    "SELECT * FROM t FOR KEY SHARE NOWAIT", "VALUES (1) FOR UPDATE",
    "SELECT * FROM (SELECT * FROM t FOR UPDATE) s",
    "WITH x AS (SELECT * FROM t FOR UPDATE) SELECT * FROM x",
    "SELECT * FROM t WHERE id IN (SELECT id FROM u FOR UPDATE)",
    "SELECT * FROM t WHERE EXISTS (SELECT 1 FROM u FOR SHARE)",
    "SELECT 1 UNION (SELECT 1 FROM t FOR UPDATE)",
    "SELECT (SELECT a FROM t LIMIT 1 FOR UPDATE)",
    "WITH d AS (DELETE FROM t RETURNING *) SELECT * FROM d",
    "WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT 1",
    "WITH i AS (INSERT INTO t VALUES (1) RETURNING *) SELECT 1",
    "WITH m AS (MERGE INTO t USING s ON t.id = s.id WHEN MATCHED THEN DELETE RETURNING t.*) SELECT * FROM m",
    "WITH x AS (WITH d AS (DELETE FROM t RETURNING id) SELECT id FROM d) SELECT * FROM x",
    "SELECT * FROM (WITH d AS (DELETE FROM t RETURNING id) SELECT id FROM d) s",
    "SELECT * FROM t WHERE id IN (WITH d AS (UPDATE t SET a = 1 RETURNING id) SELECT id FROM d)",
    "EXPLAIN SELECT 1", "EXPLAIN ANALYZE DELETE FROM t",
    "SET statement_timeout = 0", "SET TRANSACTION READ WRITE", "SET SESSION AUTHORIZATION postgres", "RESET ALL",
    "COPY t TO STDOUT", "COPY (SELECT 1) TO '/tmp/x'", "COPY t FROM PROGRAM 'id'",
    "DO $$BEGIN DELETE FROM t; END$$", "CALL p()",
    "BEGIN", "COMMIT", "ROLLBACK", "LOCK TABLE t", "VACUUM t", "ANALYZE t",
    "PREPARE p AS DELETE FROM t", "EXECUTE p", "DECLARE c CURSOR FOR SELECT 1",
    "LISTEN x", "NOTIFY x", "REFRESH MATERIALIZED VIEW mv", "CHECKPOINT", "DISCARD ALL",
]


@pytest.mark.parametrize("sql", SELECTS)
def test_s2_check_select_accepts_reads(sql):
    assert db.check_select(sql) is None


@pytest.mark.parametrize("sql", NOT_SELECTS)
def test_s2_check_select_rejects_everything_else(sql):
    with pytest.raises(ValueError):
        db.check_select(sql)


@pytest.mark.parametrize("sql", [
    "DELETE FROM clinics", "UPDATE clinics SET name = 'x'", "SELECT * FROM clinics FOR UPDATE",
    "WITH d AS (DELETE FROM clinics RETURNING *) SELECT * FROM d", "SELECT * INTO x FROM clinics",
    "EXPLAIN ANALYZE DELETE FROM clinics", "SET transaction_read_only = off",
])
def test_s3_live_explain_rejects_before_connecting(live, sql):
    with pytest.raises(ValueError):
        db.explain(sql)


@pytest.mark.parametrize("sql,ddl", [
    ("DELETE FROM clinics", []),
    ("WITH d AS (DELETE FROM clinics RETURNING *) SELECT * FROM d", []),
    ("SELECT 1; SELECT 2", []),
    ("SELECT * FROM visits WHERE animal_id = 1", ["DROP TABLE visits"]),
    ("SELECT * FROM visits WHERE animal_id = 1", ["CREATE INDEX CONCURRENTLY p ON visits (animal_id)"]),
    ("SELECT * FROM visits WHERE animal_id = 1", ["CREATE INDEX p ON visits (animal_id)", "DROP TABLE visits"]),
    ("DELETE FROM visits", ["CREATE INDEX p ON visits (animal_id)"]),
])
def test_s3_s7_s8_live_verify_fix_rejects_before_connecting(live, sql, ddl):
    result = db.verify_fix(sql, ddl, 10, 100.0)
    assert result.keys() == {"error"} and isinstance(result["error"], str) and result["error"]


@pytest.mark.parametrize("url", [
    "postgresql://u:pw@localhost:5432/db",
    "postgresql://u:pw@127.0.0.1/db",
    "postgresql://u:pw@[::1]:5432/db",
    "postgresql:///db",
    "postgresql:///db?host=/var/run/postgresql",
    "host=/tmp dbname=db user=u",
    "postgresql://u:pw@db.example.com/db?sslmode=verify-ca",
    "postgresql://u:pw@localhost/db?sslmode=require",
    "postgresql://u:pw@10.1.2.3:6432/db?sslmode=verify-full",
    "host=db.example.com dbname=db sslmode=verify-full",
])
def test_s5_tls_ok(url):
    assert db.tls_problem(url) is None


@pytest.mark.parametrize("url", [
    "postgresql://u:S3cret-PW@db.example.com/db",
    "postgresql://u:S3cret-PW@db.example.com/db?sslmode=prefer",
    "postgresql://u:S3cret-PW@db.example.com/db?sslmode=require",
    "host=db.example.com dbname=db password=S3cret-PW sslmode=require",
    "postgresql://u:S3cret-PW@db.example.com/db?sslmode=allow",
    "postgresql://u:S3cret-PW@db.example.com/db?sslmode=disable",
    "postgresql://u:S3cret-PW@10.0.0.5/db",
    "postgresql://u:S3cret-PW@localhost.evil.com/db",
    "postgresql://u:S3cret-PW@127.0.0.1.nip.io/db",
    "postgresql://u:S3cret-PW@localhost,db.example.com/db",
    "host=db.example.com dbname=db password=S3cret-PW sslmode=prefer",
])
def test_s5_tls_problem(url):
    problem = db.tls_problem(url)
    assert isinstance(problem, str) and problem
    assert "S3cret-PW" not in problem


@pytest.mark.parametrize("url,env,problem", [
    ("postgresql:///db", {"PGHOST": "db.example.com"}, True),
    ("postgresql://u@/db", {"PGHOST": "db.example.com"}, True),
    ("postgresql:///db", {"PGHOSTADDR": "10.0.0.5"}, True),
    ("postgresql://u@localhost/db", {"PGHOSTADDR": "10.0.0.5"}, True),
    ("postgresql:///db", {"PGHOST": "db.example.com", "PGSSLMODE": "prefer"}, True),
    ("postgresql:///db", {"PGHOST": "db.example.com", "PGSSLMODE": "require"}, True),
    ("postgresql:///db", {"PGHOST": "db.example.com", "PGSSLMODE": "verify-ca"}, False),
    ("postgresql://u:pw@db.example.com/db", {"PGSSLMODE": "verify-full"}, False),
    ("postgresql://u:pw@db.example.com/db?sslmode=disable", {"PGSSLMODE": "require"}, True),
    ("postgresql://u:pw@localhost/db", {"PGHOST": "db.example.com"}, False),
    ("postgresql:///db?host=/tmp", {"PGHOST": "db.example.com"}, False),
    ("postgresql://u@localhost/db?hostaddr=127.0.0.1", {"PGHOSTADDR": "10.0.0.6"}, False),
    ("postgresql:///db", {"PGHOST": "/var/run/postgresql"}, False),
    ("postgresql:///db", {"PGSSLMODE": "disable"}, False),
    ("postgresql://u@db.example.com/db?sslmode=verify-full", {"PGHOSTADDR": "10.0.0.5", "PGSSLMODE": "disable"}, False),
    ("postgresql://u@db.example.com/db?sslmode=require", {"PGSSLMODE": "verify-full"}, True),
])
def test_a3_tls_problem_uses_libpq_environment_for_what_the_url_omits(monkeypatch, url, env, problem):
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert (db.tls_problem(url) is not None) is problem


EXPLOIT = "SELECT count(*) FROM clinics WHERE (SELECT (s.x).pg_terminate_backend FROM (SELECT 4242 AS x) s)"


@pytest.mark.parametrize("sql", ["SELECT * FROM visits WHERE animal_id = 4242 ORDER BY id", EXPLOIT, "SELECT pg_sleep(10)"])
def test_b1_live_rewrite_is_offered_for_testing_without_touching_the_database(live, sql):
    result = db.verify_fix(sql, [], 7, 100.0)
    assert result.keys() == {"skipped", "testable"}
    assert result["testable"] is True and isinstance(result["skipped"], str) and result["skipped"]


NUL_SQL = ["SELECT 1\x00, pg_sleep(1)", "SELECT * FROM owners WHERE email = 'a@b.c'\x00 OR email = 'secret' /* note */",
           "\x00", "SELECT 1\x00 FOR UPDATE"]


@pytest.mark.parametrize("sql", NUL_SQL)
def test_b3_nul_rejected_by_explain_guard_and_check_select(mode, sql):
    with pytest.raises(ValueError):
        db.explain(sql)
    with pytest.raises(ValueError):
        db.check_select(sql)
    assert db.verify_fix(sql, [], 1, 1.0).keys() == {"error"}


@pytest.mark.parametrize("path", ["/api/explain", "/api/optimize"])
def test_b3_nul_query_gives_400_and_nothing_reaches_claude(mode, monkeypatch, path):
    monkeypatch.setattr(llm, "propose", lambda *a: pytest.fail("Claude must not be called"))
    res = client.post(path, json={"sql": NUL_SQL[1]})
    assert res.status_code == 400


@pytest.mark.parametrize("url,env,local", [
    ("postgresql://u:pw@host.docker.internal:5432/db", {}, True),
    ("postgresql://u:pw@HOST.Docker.Internal/db", {}, True),
    ("host=host.docker.internal dbname=db", {}, True),
    ("host=localhost hostaddr=host.docker.internal dbname=db", {}, True),
    ("postgresql:///db", {"PGHOST": "host.docker.internal"}, True),
    ("postgresql://u:pw@host.docker.internal/db?sslmode=disable", {}, True),
    ("postgresql://u:pw@host.docker.internal.evil.com/db", {}, False),
    ("postgresql://u:pw@xhost.docker.internal/db", {}, False),
    ("postgresql://u:pw@evil.host.docker.internal/db", {}, False),
    ("postgresql://u:pw@host-docker-internal/db", {}, False),
    ("host=localhost hostaddr=host.docker.internal.evil.com dbname=db", {}, False),
    ("postgresql:///db", {"PGHOST": "xhost.docker.internal"}, False),
])
def test_p2_docker_host_alias_is_local_and_look_alikes_are_not(monkeypatch, url, env, local):
    for var, value in env.items():
        monkeypatch.setenv(var, value)
    assert (db.tls_problem(url) is None) is local
