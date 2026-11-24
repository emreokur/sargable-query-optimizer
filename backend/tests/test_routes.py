import copy
import json

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import llm
import main

client = TestClient(main.app, base_url="http://localhost")
DASH = chr(0x2014)
CANARY = "Canary-PW-90210"
SQL = ("SELECT * FROM visits v JOIN animals a ON a.id = v.animal_id\n"
       "WHERE v.animal_id = 4242 AND v.status = 'open' -- ticket 'Rex-77'")
MASKED = ("SELECT * FROM visits v JOIN animals a ON a.id = v.animal_id\n"
          "WHERE v.animal_id = $1 AND v.status = $2  ")
LITERALS = ["4242", "open", "Rex-77"]


def node(id, type, total, children=(), **extra):
    return {"id": id, "type": type, "totalMs": total, "selfMs": total - sum(c["totalMs"] for c in children),
            "rows": 7, "planRows": 5, "loops": 1, "children": list(children), **extra}


PLAN = node(0, "Hash Join", 120.0, [
    node(1, "Seq Scan", 100.0, relation="visits", detail="((animal_id = 4242) AND (status = 'open'::text))",
         rowsRemoved=1499993),
    node(2, "Hash", 10.0, [node(3, "Index Scan", 10.0, relation="animals", index="animals_pkey", detail="(id = 4242)")]),
], detail="(v.animal_id = a.id)")
EXPLAIN = {"plan": PLAN, "bottleneckId": 1, "planningMs": 0.2, "executionMs": 120.5, "planCost": 35210.5}
MASKED_PLAN = copy.deepcopy(PLAN)
MASKED_PLAN["children"][0]["detail"] = "((animal_id = ?) AND (status = ?::text))"
MASKED_PLAN["children"][1]["children"][0]["detail"] = "(id = ?)"
AFTER_PLAN = node(0, "Index Scan", 0.1, relation="visits", index="probe")
SCHEMA = [{"name": n, "rows": 10, "columns": [{"name": "id", "type": "integer"}], "indexes": [f"{n}_pkey"]}
          for n in ("animals", "clinics", "owners", "visits")]
ESTIMATE = {"beforeCost": 35210.5, "afterCost": 12.5, "usesIndex": True}
SKIPPED = "Index fixes are not built on a live database; install HypoPG for an estimate or use a sandbox copy."


@pytest.fixture
def calls(monkeypatch):
    monkeypatch.setattr(db, "SANDBOX", True)
    monkeypatch.setattr(db, "DEMO", True)
    calls = {"explain": [], "returned": [], "propose": [], "verify": [], "schema": 0}

    def explain(sql):
        calls["explain"].append(sql)
        calls["returned"].append(copy.deepcopy(EXPLAIN))
        return calls["returned"][-1]

    def schema():
        calls["schema"] += 1
        return copy.deepcopy(SCHEMA)

    def propose(sql, explain, schema, live):
        calls["propose"].append((sql, copy.deepcopy(explain), copy.deepcopy(schema), live))
        return llm.Proposal(diagnosis=f"Seq scan for animal $1 {DASH} status $2", fixes=[
            llm.ProposedFix(title=f"Index for animal $1 {DASH} fast", kind="index", rationale="Rows with status $2",
                            ddl=["CREATE INDEX ON visits (animal_id) WHERE status = $2"], rewritten_sql=None),
            llm.ProposedFix(title="Drop it", kind="index", rationale="Bad", ddl=["DROP TABLE visits"], rewritten_sql=None),
            llm.ProposedFix(title="Rewrite", kind="rewrite", rationale="Keeps $9 untouched", ddl=[],
                            rewritten_sql="SELECT v.* FROM visits v WHERE v.animal_id = $1 AND v.status = $2 LIMIT $10"),
            llm.ProposedFix(title="Owner index", kind="index", rationale="Join", ddl=["CREATE INDEX ON animals (owner_id)"],
                            rewritten_sql=None),
        ])

    def verify_fix(sql, ddl, before_rows, before_cost):
        calls["verify"].append((sql, ddl, before_rows, before_cost))
        if ddl == ["DROP TABLE visits"]:
            return {"error": "only CREATE INDEX is allowed"}
        if ddl == ["CREATE INDEX ON animals (owner_id)"]:
            return {"skipped": SKIPPED}
        if ddl:
            return {"estimate": ESTIMATE}
        return {"afterMs": 0.3, "plan": AFTER_PLAN, "rowsMatch": True}

    monkeypatch.setattr(db, "explain", explain)
    monkeypatch.setattr(db, "schema", schema)
    monkeypatch.setattr(llm, "propose", propose)
    monkeypatch.setattr(db, "verify_fix", verify_fix)
    return calls


def test_f7_s11_f12_optimize_unmasks_verifies_every_fix_and_strips_em_dashes(calls):
    res = client.post("/api/optimize", json={"sql": SQL})
    assert res.status_code == 200
    rewrite = "SELECT v.* FROM visits v WHERE v.animal_id = 4242 AND v.status = 'open' LIMIT $10"
    assert res.json() == {
        "diagnosis": "Seq scan for animal 4242 - status 'open'",
        "beforeMs": 120.5,
        "fixes": [
            {"title": "Index for animal 4242 - fast", "kind": "index", "rationale": "Rows with status 'open'",
             "ddl": ["CREATE INDEX ON visits (animal_id) WHERE status = 'open'"], "sql": SQL, "estimate": ESTIMATE},
            {"title": "Drop it", "kind": "index", "rationale": "Bad", "ddl": ["DROP TABLE visits"],
             "sql": SQL, "error": "only CREATE INDEX is allowed"},
            {"title": "Rewrite", "kind": "rewrite", "rationale": "Keeps $9 untouched", "ddl": [], "sql": rewrite,
             "afterMs": 0.3, "plan": AFTER_PLAN, "rowsMatch": True},
            {"title": "Owner index", "kind": "index", "rationale": "Join", "ddl": ["CREATE INDEX ON animals (owner_id)"],
             "sql": SQL, "skipped": SKIPPED},
        ],
    }
    assert DASH not in res.text
    assert calls["explain"] == [SQL]
    assert calls["verify"] == [
        (SQL, ["CREATE INDEX ON visits (animal_id) WHERE status = 'open'"], 7, 35210.5),
        (SQL, ["DROP TABLE visits"], 7, 35210.5),
        (rewrite, [], 7, 35210.5),
        (SQL, ["CREATE INDEX ON animals (owner_id)"], 7, 35210.5),
    ]


@pytest.mark.parametrize("sandbox", [True, False])
def test_s10_claude_gets_masked_query_and_plan_and_only_plan_tables(calls, monkeypatch, sandbox):
    monkeypatch.setattr(db, "SANDBOX", sandbox)
    monkeypatch.setattr(db, "DEMO", False)
    assert client.post("/api/optimize", json={"sql": SQL}).status_code == 200
    [(sql, explain, schema, live)] = calls["propose"]
    assert sql == MASKED
    assert explain == {**EXPLAIN, "plan": MASKED_PLAN}
    assert sorted(schema, key=lambda t: t["name"]) == [SCHEMA[0], SCHEMA[3]]
    assert live is (not sandbox)
    sent = json.dumps(calls["propose"])
    assert not [lit for lit in LITERALS if lit in sent]
    assert calls["returned"] == [EXPLAIN]


@pytest.mark.parametrize("sql", ["SELECT * FROM visits WHERE animal_id = $1", "SELECT col$1 FROM visits WHERE id = 5"])
def test_s9_unmaskable_query_gives_400_and_nothing_reaches_claude(calls, sql):
    res = client.post("/api/optimize", json={"sql": sql})
    assert res.status_code == 400 and res.json()["detail"]
    assert calls["propose"] == [] and calls["verify"] == []


def test_f7_llm_error_gives_502_with_message(calls, monkeypatch):
    def fail(sql, explain, schema, live):
        raise llm.LLMError("Claude declined to answer this request")

    monkeypatch.setattr(llm, "propose", fail)
    res = client.post("/api/optimize", json={"sql": SQL})
    assert res.status_code == 502
    assert res.json() == {"detail": "Claude declined to answer this request"}
    assert calls["verify"] == []


@pytest.mark.parametrize("path", ["/api/explain", "/api/optimize"])
@pytest.mark.parametrize("error,status", [
    (ValueError("only one statement is allowed"), 400),
    (psycopg.errors.SyntaxError('syntax error at or near "SELEC"'), 400),
    (db.Refused("The role can write: it is a superuser."), 403),
])
def test_f2_f7_s4_explain_errors_map_to_status_with_message(calls, monkeypatch, path, error, status):
    def fail(sql):
        raise error

    monkeypatch.setattr(db, "explain", fail)
    res = client.post(path, json={"sql": SQL})
    assert res.status_code == status
    assert res.json() == {"detail": str(error)}
    assert calls["propose"] == [] and calls["verify"] == []


def test_f2_explain_route_passes_valid_result_through(calls):
    res = client.post("/api/explain", json={"sql": SQL})
    assert res.status_code == 200
    assert res.json() == EXPLAIN
    assert calls["explain"] == [SQL]


def test_f8_schema_route_returns_schema(calls):
    res = client.get("/api/schema")
    assert res.status_code == 200
    assert res.json() == SCHEMA


def test_s12_connection_route_returns_preflight(monkeypatch):
    info = {"host": "localhost", "port": "5432", "database": "vet", "user": "postgres", "serverVersion": "17.6",
            "mode": "sandbox", "demo": True, "hypopg": True, "problems": []}
    monkeypatch.setattr(db, "preflight", lambda: info)
    res = client.get("/api/connection")
    assert res.status_code == 200 and res.json() == info


@pytest.mark.parametrize("base_url", ["http://evil.example.com", "http://localhost.evil.com", "http://127.0.0.2:8000"])
def test_s12_foreign_host_is_rejected_before_any_handler(calls, base_url):
    foreign = TestClient(main.app, base_url=base_url)
    assert foreign.get("/api/schema").status_code == 400
    assert foreign.post("/api/explain", json={"sql": "SELECT 1"}).status_code == 400
    assert calls["schema"] == 0 and calls["explain"] == []
    assert TestClient(main.app, base_url="http://127.0.0.1:8000").get("/api/schema").status_code == 200


@pytest.mark.parametrize("content_type", ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"])
def test_s12_non_json_body_is_rejected(calls, content_type):
    res = client.post("/api/explain", content=json.dumps({"sql": "SELECT 1"}), headers={"Content-Type": content_type})
    assert 400 <= res.status_code < 500
    assert calls["explain"] == []


def test_s12_no_cors_headers(calls):
    origin = {"Origin": "http://evil.example.com"}
    responses = [
        client.post("/api/explain", json={"sql": "SELECT 1"}, headers=origin),
        client.get("/api/schema", headers=origin),
        client.options("/api/explain", headers={**origin, "Access-Control-Request-Method": "POST",
                                                 "Access-Control-Request-Headers": "content-type"}),
    ]
    for res in responses:
        assert not [h for h in res.headers if h.lower().startswith("access-control-")]
    assert responses[2].status_code >= 400


@pytest.mark.parametrize("sandbox", [True, False])
def test_s12_password_never_in_responses_when_database_unreachable(monkeypatch, sandbox):
    monkeypatch.setattr(db, "URL", f"postgresql://app_user:{CANARY}@127.0.0.1:1/appdb?connect_timeout=2")
    monkeypatch.setattr(db, "SANDBOX", sandbox)
    monkeypatch.setattr(db, "DEMO", False)
    monkeypatch.setattr(llm, "propose", lambda *a: pytest.fail("Claude must not be called"))
    responses = [client.get("/api/connection")] + [client.post(p, json={"sql": "SELECT 1"}) for p in ("/api/explain", "/api/optimize")]
    assert responses[0].status_code == 200
    expected = 400 if sandbox else 403
    assert [r.status_code for r in responses[1:]] == [expected, expected]
    for res in responses:
        assert CANARY not in res.text


@pytest.mark.parametrize("sandbox", [False, True])
def test_a2_live_rewrite_calling_a_new_function_is_an_error_and_never_verified(calls, monkeypatch, sandbox):
    monkeypatch.setattr(db, "SANDBOX", sandbox)
    monkeypatch.setattr(db, "DEMO", False)
    checked = []
    real_check = db.check_rewrite

    def check_rewrite(original, rewrite):
        checked.append((original, rewrite))
        return real_check(original, rewrite)

    monkeypatch.setattr(db, "check_rewrite", check_rewrite)
    sleepy = "SELECT v.*, pg_sleep($1) FROM visits v WHERE v.animal_id = $1"
    counted = "SELECT count(*) FROM visits v WHERE v.animal_id = $1 AND v.status = $2"
    monkeypatch.setattr(llm, "propose", lambda sql, explain, schema, live: llm.Proposal(diagnosis="d", fixes=[
        llm.ProposedFix(title="Index", kind="index", rationale="r", ddl=["CREATE INDEX ON visits (animal_id)"],
                        rewritten_sql=None),
        llm.ProposedFix(title="Sleepy", kind="rewrite", rationale="r", ddl=[], rewritten_sql=sleepy),
        llm.ProposedFix(title="Count", kind="rewrite", rationale="r", ddl=[], rewritten_sql=counted),
    ]))
    res = client.post("/api/optimize", json={"sql": SQL})
    assert res.status_code == 200
    fixes = res.json()["fixes"]
    unmasked = [s.replace("$1", "4242").replace("$2", "'open'") for s in (sleepy, counted)]
    verified = [(SQL, ["CREATE INDEX ON visits (animal_id)"], 7, 35210.5), (unmasked[1], [], 7, 35210.5)]
    if sandbox:
        assert checked == []
        verified.insert(1, (unmasked[0], [], 7, 35210.5))
        assert "error" not in fixes[1]
    else:
        assert checked == [(SQL, unmasked[0]), (SQL, unmasked[1])]
        assert fixes[1].keys() == {"title", "kind", "rationale", "ddl", "sql", "error"}
        assert fixes[1]["sql"] == unmasked[0] and "pg_sleep" in fixes[1]["error"]
    assert calls["verify"] == verified
    assert "afterMs" in fixes[2] and "estimate" in fixes[0]
