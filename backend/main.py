import copy
import os

from typing import Any

import psycopg
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import db
import llm
import mask

CONNECTION_FIELDS = {"host", "port", "database", "user", "password", "sslmode", "sandbox", "ca_cert"}
STATIC = os.environ.get("SARGABLE_STATIC", os.path.join(os.path.dirname(__file__), "static"))
ALLOWED_HOSTS = ["localhost", "127.0.0.1"] + [h for h in os.environ.get("SARGABLE_ALLOWED_HOSTS", "").split(",") if h]

app = FastAPI()
app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)


class Query(BaseModel):
    sql: str


def undash(text):
    return text.replace(chr(0x2014), "-")


def nodes(plan):
    yield plan
    for child in plan["children"]:
        yield from nodes(child)


def explain_or_raise(sql):
    try:
        return db.explain(sql)
    except db.Refused as e:
        raise HTTPException(403, str(e))
    except (ValueError, psycopg.Error) as e:
        raise HTTPException(400, str(e))


def verify(original, rewrite, ddl, ex):
    if rewrite and not db.SANDBOX:
        try:
            db.check_rewrite(original, rewrite)
        except ValueError as e:
            return {"error": str(e)}
    return db.verify_fix(rewrite or original, ddl, ex["plan"]["rows"], ex["planCost"])


@app.get("/api/connection")
def connection():
    return db.preflight()


@app.post("/api/connection")
def connect(body: Any = Body(None)):
    if not isinstance(body, dict) or body.keys() != CONNECTION_FIELDS:
        raise HTTPException(400, "Send a JSON object with exactly host, port, database, user, password, sslmode, sandbox, ca_cert.")
    try:
        db.configure(**body)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return db.preflight()


@app.delete("/api/connection")
def disconnect():
    db.reset()
    return db.preflight()


@app.get("/api/schema")
def schema():
    try:
        return db.schema()
    except db.Refused as e:
        raise HTTPException(403, str(e))
    except psycopg.Error as e:
        raise HTTPException(400, str(e))


@app.post("/api/explain")
def explain(q: Query):
    return explain_or_raise(q.sql)


@app.post("/api/optimize")
def optimize(q: Query):
    ex = explain_or_raise(q.sql)
    try:
        masked, values = mask.mask_sql(q.sql)
    except ValueError as e:
        raise HTTPException(400, str(e))
    sent = copy.deepcopy(ex)
    relations = set()
    for node in nodes(sent["plan"]):
        if "detail" in node:
            node["detail"] = mask.mask_text(node["detail"])
        relations.add(node.get("relation"))
    tables = [t for t in db.schema() if t["name"].split(".")[-1] in relations]
    try:
        proposal = llm.propose(masked, sent, tables, not db.SANDBOX)
    except llm.LLMError as e:
        raise HTTPException(502, str(e))

    def prose(text):
        return undash(mask.unmask(text, values))

    fixes = []
    for fix in proposal.fixes:
        ddl = [mask.unmask(stmt, values) for stmt in fix.ddl]
        rewrite = mask.unmask(fix.rewritten_sql, values) if fix.rewritten_sql else None
        fixes.append({"title": prose(fix.title), "kind": fix.kind, "rationale": prose(fix.rationale), "ddl": ddl,
                      "sql": rewrite or q.sql, **verify(q.sql, rewrite, ddl, ex)})
    return {"diagnosis": prose(proposal.diagnosis), "beforeMs": ex["executionMs"], "fixes": fixes}


if os.path.isdir(STATIC):
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
