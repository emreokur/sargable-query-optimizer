import os
import ssl
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

import pglast
import psycopg
from pglast import ast
from psycopg.conninfo import conninfo_to_dict, make_conninfo

URL = os.environ.get("DATABASE_URL", os.environ.get("SARGABLE_DEMO_URL", "postgresql://postgres:postgres@localhost:5432/vet"))
DEMO = "DATABASE_URL" not in os.environ
SANDBOX = DEMO or os.environ.get("SARGABLE_SANDBOX") == "1"
STARTUP = (URL, DEMO, SANDBOX)
CA_FILE = None
TIMEOUT_MS = 15000
LOCK = threading.RLock()
DETAIL_KEYS = ("Filter", "Index Cond", "Recheck Cond", "Hash Cond", "Merge Cond", "Join Filter", "Sort Key")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "", "host.docker.internal") + tuple(
    h.strip().lower() for h in os.environ.get("SARGABLE_LOCAL_HOSTS", "").split(",") if h.strip())
SAFE_FUNCTIONS = {"count", "sum", "avg", "min", "max", "bool_and", "bool_or", "row_number", "rank", "dense_rank", "lag",
                  "lead", "first_value", "last_value", "lower", "upper", "length", "abs", "round", "date_trunc", "now",
                  "extract"}
BAD_URL = "DATABASE_URL is not a valid connection string. Percent-encode special characters in the password."
UNTESTED = "Rewrites from Claude are not run automatically on a live database; review it, then test it."
SKIPPED = ("Index fixes are not built on a live database; install HypoPG there for a planner estimate, "
           "or point a sandbox copy at Sargable for a measurement.")
ROLE_PROBLEMS = (
    "The role is a superuser.",
    "The role has CREATEROLE, CREATEDB or REPLICATION.",
    "The role is a member of a predefined role that can write, read server files, signal backends or run maintenance.",
    "The role owns, or holds a privilege other than SELECT on, at least one table, view, sequence or column.",
    "The role owns, or can CREATE in, the database or at least one schema.",
)
PREFLIGHT = """
WITH m AS (SELECT * FROM pg_roles WHERE pg_has_role(current_user, oid, 'MEMBER')),
g AS (SELECT oid FROM m UNION ALL SELECT 0),
s AS (SELECT bool_or(rolsuper) AS su FROM m),
rel AS (SELECT c.* FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname NOT IN ('pg_catalog', 'information_schema') AND c.relkind IN ('r', 'p', 'v', 'm', 'f', 'S'))
SELECT current_setting('server_version'),
  EXISTS (SELECT FROM pg_extension WHERE extname = 'hypopg'),
  s.su,
  EXISTS (SELECT FROM m WHERE rolcreaterole OR rolcreatedb OR rolreplication),
  EXISTS (SELECT FROM m WHERE rolname IN ('pg_write_all_data', 'pg_write_server_files', 'pg_read_server_files',
          'pg_execute_server_program', 'pg_maintain', 'pg_signal_backend', 'pg_checkpoint', 'pg_create_subscription')),
  s.su OR EXISTS (SELECT FROM rel WHERE relowner IN (SELECT oid FROM m))
    OR EXISTS (SELECT FROM rel, aclexplode(rel.relacl) a
               WHERE a.grantee IN (SELECT oid FROM g) AND a.privilege_type <> 'SELECT')
    OR EXISTS (SELECT FROM rel JOIN pg_attribute att ON att.attrelid = rel.oid, aclexplode(att.attacl) a
               WHERE a.grantee IN (SELECT oid FROM g) AND a.privilege_type <> 'SELECT'),
  s.su OR EXISTS (SELECT FROM pg_namespace n
          WHERE n.nspname NOT IN ('pg_catalog', 'information_schema')
            AND NOT starts_with(n.nspname, 'pg_temp') AND NOT starts_with(n.nspname, 'pg_toast')
            AND (n.nspowner IN (SELECT oid FROM m)
                 OR EXISTS (SELECT FROM aclexplode(n.nspacl) a
                            WHERE a.grantee IN (SELECT oid FROM g) AND a.privilege_type = 'CREATE')))
    OR EXISTS (SELECT FROM pg_database d
               WHERE d.datname = current_database()
                 AND (d.datdba IN (SELECT oid FROM m)
                      OR EXISTS (SELECT FROM aclexplode(d.datacl) a
                                 WHERE a.grantee IN (SELECT oid FROM g) AND a.privilege_type = 'CREATE')))
FROM s"""


class Refused(Exception):
    pass


def annotate(raw):
    nodes = []

    def walk(r):
        node = {"id": len(nodes), "type": r["Node Type"]}
        nodes.append(node)
        if "Relation Name" in r:
            node["relation"] = r["Relation Name"]
        if "Index Name" in r:
            node["index"] = r["Index Name"]
        detail = next((r[k] for k in DETAIL_KEYS if k in r), None)
        if detail is not None:
            node["detail"] = ", ".join(detail) if isinstance(detail, list) else detail
        loops = r["Actual Loops"]
        children = [walk(c) for c in r.get("Plans", [])]
        total = r["Actual Total Time"] * loops
        node.update(totalMs=total, selfMs=max(0.0, total - sum(c["totalMs"] for c in children)),
                    rows=round(r["Actual Rows"] * loops), planRows=r["Plan Rows"], loops=loops)
        if "Rows Removed by Filter" in r:
            node["rowsRemoved"] = round(r["Rows Removed by Filter"] * loops)
        node["children"] = children
        return node

    plan = walk(raw)
    return plan, max(nodes, key=lambda n: n["selfMs"])["id"]


def _strip(sql):
    sql = sql.strip()
    return sql[:-1] if sql.endswith(";") else sql


def _guard(sql):
    if "\x00" in sql:
        raise ValueError("The query contains a NUL byte.")
    sql = _strip(sql)
    if not sql:
        raise ValueError("query is empty")
    if ";" in sql:
        raise ValueError("only one statement is allowed")
    return sql


def check_ddl(stmt):
    s = _strip(stmt).upper()
    if not s.startswith(("CREATE INDEX", "CREATE UNIQUE INDEX")) or ";" in s or "CONCURRENTLY" in s:
        raise ValueError("only a single CREATE INDEX statement without CONCURRENTLY is allowed")


def _walk(node):
    if isinstance(node, tuple):
        for item in node:
            yield from _walk(item)
    elif isinstance(node, ast.Node):
        yield node
        for field in node:
            yield from _walk(getattr(node, field))


def _parse(sql):
    try:
        return pglast.parse_sql(sql)
    except pglast.parser.ParseError as e:
        raise ValueError(f"The query cannot be parsed: {e}")


def check_select(sql):
    if "\x00" in sql:
        raise ValueError("The query contains a NUL byte.")
    stmts = _parse(sql)
    if len(stmts) != 1 or not isinstance(stmts[0].stmt, ast.SelectStmt):
        raise ValueError("Only a single SELECT statement is allowed on a live database.")
    for node in _walk(stmts[0].stmt):
        if isinstance(node, (ast.InsertStmt, ast.UpdateStmt, ast.DeleteStmt, ast.MergeStmt)):
            raise ValueError("Data-modifying statements are not allowed on a live database.")
        if isinstance(node, ast.SelectStmt) and (node.intoClause or node.lockingClause):
            raise ValueError("SELECT INTO and locking clauses are not allowed on a live database.")


def _calls(sql):
    return {".".join(s.sval for s in n.funcname).lower() for n in _walk(_parse(sql)) if isinstance(n, ast.FuncCall)}


def check_rewrite(original_sql, rewrite_sql):
    for name in sorted(_calls(rewrite_sql) - _calls(original_sql)):
        if name.removeprefix("pg_catalog.") not in SAFE_FUNCTIONS:
            raise ValueError(f"The rewrite calls {name}, which the original query does not; it was not run.")


def tls_problem(url):
    env = {k: os.environ[f"PG{k.upper()}"] for k in ("host", "hostaddr", "sslmode", "service")
           if os.environ.get(f"PG{k.upper()}")}
    info = env | {k: v for k, v in conninfo_to_dict(url).items() if v}
    if "service" in info:
        return "DATABASE_URL uses a service file, so its host and TLS settings cannot be checked: put them in the URL."
    if info.get("sslmode") in ("verify-ca", "verify-full"):
        return None
    if all(h.lower() in LOCAL_HOSTS or h.startswith("/") for h in (info.get("host", ""), info.get("hostaddr", ""))):
        return None
    return (f"The server at {info.get('host', '')} is not verified, so the password could be intercepted: "
            "use SSL mode verify-full.")


def _conninfo():
    try:
        info = conninfo_to_dict(URL)
    except psycopg.Error:
        raise Refused(BAD_URL) from None
    if "@" in info.get("host", ""):
        raise Refused(BAD_URL)
    return info


def configure(host, port, database, user, password, sslmode, sandbox, ca_cert):
    global URL, DEMO, SANDBOX, CA_FILE
    for name, value in (("host", host), ("database", database), ("user", user)):
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValueError(f"{name} must be a non-empty string without NUL bytes.")
    if "@" in host:
        raise ValueError("host must not contain @.")
    if not isinstance(password, str) or "\x00" in password:
        raise ValueError("password must be a string without NUL bytes.")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("port must be an integer from 1 to 65535.")
    if sslmode not in ("disable", "require", "verify-full"):
        raise ValueError("sslmode must be disable, require or verify-full.")
    if not isinstance(sandbox, bool):
        raise ValueError("sandbox must be true or false.")
    if not isinstance(ca_cert, str):
        raise ValueError("ca_cert must be a string.")
    if ca_cert and sslmode != "verify-full":
        raise ValueError("ca_cert is only allowed with sslmode verify-full.")
    if len(ca_cert.encode()) > 65536 or "\x00" in ca_cert:
        raise ValueError("ca_cert must be a PEM certificate of at most 64 KB.")
    if ca_cert:
        try:
            ssl.create_default_context(cadata=ca_cert)
        except (ssl.SSLError, ValueError):
            raise ValueError("ca_cert is not a valid PEM certificate.") from None
    root = "system" if sslmode == "verify-full" else None
    if ca_cert:
        fd, root = tempfile.mkstemp(suffix=".pem")
        with os.fdopen(fd, "w") as f:
            f.write(ca_cert)
    url = make_conninfo(host=host, port=port, dbname=database, user=user, password=password, sslmode=sslmode,
                        sslrootcert=root)
    with LOCK:
        _forget_ca()
        URL, DEMO, SANDBOX, CA_FILE = url, False, sandbox, root if ca_cert else None


def _forget_ca():
    if CA_FILE:
        Path(CA_FILE).unlink(missing_ok=True)


def reset():
    global URL, DEMO, SANDBOX, CA_FILE
    with LOCK:
        _forget_ca()
        URL, DEMO, SANDBOX, CA_FILE = *STARTUP, None


@contextmanager
def _transaction():
    with LOCK:
        _conninfo()
        with psycopg.connect(URL, autocommit=True, connect_timeout=5) as conn:
            conn.read_only = not SANDBOX
            with conn.transaction(force_rollback=True):
                conn.execute(f"SET LOCAL statement_timeout = {int(TIMEOUT_MS)}")
                conn.execute("SET LOCAL lock_timeout = 2000")
                conn.execute("SET LOCAL max_parallel_workers_per_gather = 0")
                conn.execute("SET LOCAL standard_conforming_strings = on")
                yield conn


def preflight():
    base = {"serverVersion": None, "mode": "sandbox" if SANDBOX else "live", "demo": DEMO, "hypopg": False}
    try:
        info = _conninfo()
    except Refused as e:
        return {"host": "", "port": "", "database": "", "user": "", **base, "problems": [str(e)]}
    result = {"host": info.get("host", ""), "port": info.get("port", ""), "database": info.get("dbname", ""),
              "user": info.get("user", ""), **base, "problems": []}
    try:
        with _transaction() as conn:
            version, hypopg, *checks = conn.execute(PREFLIGHT).fetchone()
    except psycopg.OperationalError as e:
        result["problems"].append(f"Cannot connect to the database: {e}")
    else:
        result.update(serverVersion=version, hypopg=hypopg)
        if not SANDBOX:
            result["problems"] += [p for p, bad in zip(ROLE_PROBLEMS, checks) if bad]
    if not SANDBOX and tls_problem(URL):
        result["problems"].append(tls_problem(URL))
    return result


def _live_check(sql):
    check_select(sql)
    info = preflight()
    if info["problems"]:
        raise Refused("Live mode refuses to run: " + " ".join(info["problems"]))
    return info


def _analyze(sql, ddl=()):
    with _transaction() as conn:
        for stmt in ddl:
            conn.execute(stmt)
        [[out]] = conn.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql).fetchall()
    plan, bottleneck = annotate(out[0]["Plan"])
    return {"plan": plan, "bottleneckId": bottleneck, "planningMs": out[0]["Planning Time"],
            "executionMs": out[0]["Execution Time"], "planCost": out[0]["Plan"]["Total Cost"]}


def _estimate(sql, ddl, before_cost):
    with _transaction() as conn:
        for stmt in ddl:
            conn.execute("SELECT hypopg_create_index(%s)", (stmt,))
        [[out]] = conn.execute("EXPLAIN (FORMAT JSON) " + sql).fetchall()

    def uses(node):
        return node.get("Index Name", "").startswith("<") or any(uses(c) for c in node.get("Plans", []))

    plan = out[0]["Plan"]
    return {"beforeCost": before_cost, "afterCost": plan["Total Cost"], "usesIndex": uses(plan)}


def explain(sql):
    sql = _guard(sql)
    with LOCK:
        if not SANDBOX:
            _live_check(sql)
        return _analyze(sql)


def verify_fix(sql, ddl, before_rows, before_cost):
    try:
        sql = _guard(sql)
        for stmt in ddl:
            check_ddl(stmt)
        with LOCK:
            if SANDBOX:
                result = _analyze(sql, ddl)
            elif not ddl:
                check_select(sql)
                return {"skipped": UNTESTED, "testable": True}
            else:
                info = _live_check(sql)
                return {"estimate": _estimate(sql, ddl, before_cost)} if info["hypopg"] else {"skipped": SKIPPED}
    except (ValueError, Refused, psycopg.Error) as e:
        return {"error": str(e)}
    plan = result["plan"]
    return {"afterMs": result["executionMs"], "plan": plan, "rowsMatch": plan["rows"] == before_rows}


def schema():
    with _transaction() as conn:
        rows = conn.execute("""
            SELECT CASE WHEN n.nspname = 'public' THEN c.relname ELSE n.nspname || '.' || c.relname END AS name,
              round(c.reltuples)::bigint,
              (SELECT json_agg(json_build_object('name', a.attname, 'type', format_type(a.atttypid, a.atttypmod))
                               ORDER BY a.attnum)
                 FROM pg_attribute a WHERE a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped),
              (SELECT coalesce(json_agg(i.relname ORDER BY i.relname), '[]')
                 FROM pg_index x JOIN pg_class i ON i.oid = x.indexrelid WHERE x.indrelid = c.oid)
            FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind = 'r' AND n.nspname NOT IN ('pg_catalog', 'information_schema')
            ORDER BY name""").fetchall()
    return [{"name": n, "rows": r, "columns": cols, "indexes": idx} for n, r, cols, idx in rows]
