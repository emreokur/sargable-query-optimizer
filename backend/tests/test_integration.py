import os
import time

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import main

pytestmark = pytest.mark.integration
client = TestClient(main.app, base_url="http://localhost")
URL = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/vet")
COUNTS = {"animals": 400000, "clinics": 200, "drugs": 2000, "owners": 150000, "treatments": 3000000, "visits": 1500000}
COLUMNS = {
    "animals": {"id", "owner_id", "name", "species", "breed", "sex", "birth_date", "weight_kg"},
    "clinics": {"id", "name", "city"},
    "drugs": {"id", "name", "category"},
    "owners": {"id", "full_name", "email", "phone", "city", "created_at"},
    "treatments": {"id", "visit_id", "drug_id", "dose_mg", "administered_at"},
    "visits": {"id", "animal_id", "clinic_id", "visited_at", "reason", "status", "fee"},
}


@pytest.fixture(autouse=True)
def sandbox(monkeypatch):
    monkeypatch.setattr(db, "URL", URL)
    monkeypatch.setattr(db, "SANDBOX", True)
    monkeypatch.setattr(db, "DEMO", True)


def query(sql, *params):
    with psycopg.connect(URL) as conn:
        return conn.execute(sql, params).fetchall()


def preorder(node):
    yield node
    for child in node["children"]:
        yield from preorder(child)


def test_f2_trailing_semicolon_is_accepted():
    res = client.post("/api/explain", json={"sql": "  SELECT 1;  "})
    assert res.status_code == 200
    body = res.json()
    assert body["plan"]["type"] == "Result" and body["bottleneckId"] == 0
    assert body["executionMs"] >= 0 and body["planningMs"] >= 0


def test_f2_syntax_error_gives_postgres_message():
    res = client.post("/api/explain", json={"sql": "SELEC * FROM visits"})
    assert res.status_code == 400
    assert 'syntax error at or near "SELEC"' in res.json()["detail"]


def test_f2_timeout_gives_400(monkeypatch):
    monkeypatch.setattr(db, "TIMEOUT_MS", 200)
    start = time.monotonic()
    res = client.post("/api/explain", json={"sql": "SELECT pg_sleep(2)"})
    assert res.status_code == 400
    assert "statement timeout" in res.json()["detail"]
    assert time.monotonic() - start < 1.5


def test_f3_explain_does_not_modify_data():
    db.explain("DELETE FROM clinics")
    db.explain("UPDATE clinics SET name = 'probe-name'")
    assert query("SELECT count(*), count(*) FILTER (WHERE name = 'probe-name') FROM clinics") == [(200, 0)]


@pytest.mark.parametrize("sql,ddl", [
    ("SELECT * FROM clinics", ["CREATE INDEX probe_ok ON clinics (city)", "DROP TABLE clinics"]),
    ("SELECT * FROM clinics", ["CREATE INDEX CONCURRENTLY probe_ok ON clinics (city)"]),
    ("SELECT * FROM clinics", ["CREATE INDEX probe_ok ON clinics (city); DROP TABLE clinics"]),
    ("SELECT 1; SELECT 2", []),
    ("SELEC * FROM clinics", []),
])
def test_f4_verify_fix_reports_rejections_as_error(sql, ddl):
    result = db.verify_fix(sql, ddl, 200, 10.0)
    assert result.keys() == {"error"} and isinstance(result["error"], str) and result["error"]
    assert query("SELECT count(*) FROM clinics") == [(200,)]
    assert query("SELECT 1 FROM pg_indexes WHERE indexname = 'probe_ok'") == []


def test_f5_index_fix_is_faster_uses_index_and_does_not_persist():
    sql = "SELECT * FROM visits WHERE animal_id = 4242"
    before = db.explain(sql)
    result = db.verify_fix(sql, ["CREATE INDEX probe_visits_animal ON visits (animal_id)"], before["plan"]["rows"],
                           before["planCost"])
    assert before["planCost"] > 0
    assert result["afterMs"] < before["executionMs"]
    assert any(n.get("index") == "probe_visits_animal" for n in preorder(result["plan"]))
    assert result["rowsMatch"] is True
    assert query("SELECT 1 FROM pg_indexes WHERE indexname = 'probe_visits_animal'") == []


def test_f6_rewrite_with_different_row_count_is_flagged():
    before = db.explain("SELECT * FROM clinics")
    args = (before["plan"]["rows"], before["planCost"])
    assert db.verify_fix("SELECT * FROM clinics ORDER BY id", [], *args)["rowsMatch"] is True
    assert db.verify_fix("SELECT * FROM clinics WHERE id <= 10", [], *args)["rowsMatch"] is False


def test_f8_schema_route_lists_tables_columns_indexes_and_row_estimates():
    res = client.get("/api/schema")
    assert res.status_code == 200
    tables = res.json()
    assert [t["name"] for t in tables] == sorted(COUNTS)
    for t in tables:
        assert {c["name"] for c in t["columns"]} == COLUMNS[t["name"]]
        assert all(isinstance(c["type"], str) and c["type"] for c in t["columns"])
        pk = query("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = %s::regclass AND indisprimary",
                   t["name"])
        assert t["indexes"] == [pk[0][0]]
        assert abs(t["rows"] - COUNTS[t["name"]]) <= COUNTS[t["name"]] * 0.1
