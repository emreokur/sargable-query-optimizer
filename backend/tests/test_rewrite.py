import pytest

import db

SAFE = {"count", "sum", "avg", "min", "max", "bool_and", "bool_or", "row_number", "rank",
        "dense_rank", "lag", "lead", "first_value", "last_value", "lower", "upper", "length", "abs", "round",
        "date_trunc", "now", "extract"}
ORIGINAL = "SELECT * FROM visits WHERE animal_id = 4242"
SAFE_CALLS = {
    "count": "count(*)", "sum": "sum(fee)", "avg": "avg(fee)", "min": "min(fee)", "max": "max(fee)",
    "bool_and": "bool_and(fee > 0)",
    "bool_or": "bool_or(fee > 0)", "row_number": "row_number() OVER ()", "rank": "rank() OVER (ORDER BY id)",
    "dense_rank": "dense_rank() OVER (ORDER BY id)", "lag": "lag(fee) OVER (ORDER BY id)",
    "lead": "lead(fee) OVER (ORDER BY id)", "first_value": "first_value(fee) OVER (ORDER BY id)",
    "last_value": "last_value(fee) OVER (ORDER BY id)", "lower": "lower(reason)", "upper": "upper(reason)",
    "length": "length(reason)", "abs": "abs(fee)", "round": "round(fee, 1)", "date_trunc": "date_trunc('day', visited_at)",
    "now": "now()", "extract": "extract(year FROM visited_at)",
}


def test_a2_safe_functions_are_exactly_the_contract_set():
    assert set(db.SAFE_FUNCTIONS) == SAFE == set(SAFE_CALLS)


@pytest.mark.parametrize("name", sorted(SAFE))
def test_a2_safe_functions_pass_unqualified_qualified_and_in_any_case(name):
    call = SAFE_CALLS[name]
    qualified = "pg_catalog.extract('year', visited_at)" if name == "extract" else f"pg_catalog.{call}"
    for variant in (call, call.upper(), qualified, qualified.upper()):
        assert db.check_rewrite(ORIGINAL, f"SELECT {variant} FROM visits WHERE animal_id = 4242") is None


@pytest.mark.parametrize("original,rewrite", [
    (ORIGINAL, "SELECT id, fee FROM visits WHERE animal_id = 4242 ORDER BY id LIMIT 10"),
    (ORIGINAL, "SELECT 'pg_sleep(1)' AS note, id FROM visits -- pg_sleep(1)"),
    (ORIGINAL, "SELECT v.* FROM visits v JOIN animals a ON a.id = v.animal_id WHERE a.id = 4242 AND v.fee::int > 0"),
    (ORIGINAL, "SELECT coalesce(fee, 0), greatest(fee, 1), CASE WHEN fee > 1 THEN 1 END, current_date FROM visits"),
    ("SELECT my_schema.similarity(a, b) FROM t", "SELECT my_schema.similarity(a, b) FROM t WHERE id > 1"),
    ("SELECT Similarity(a, b) FROM t", "SELECT similarity(b, a) FROM t"),
    ('SELECT "Similarity"(a, b) FROM t', "SELECT similarity(b, a) FROM t"),
    (ORIGINAL, 'SELECT "COUNT"(*), "pg_catalog"."LOWER"(reason) FROM visits'),
    ("SELECT visited_at AT TIME ZONE 'utc' FROM visits", "SELECT visited_at AT TIME ZONE 'utc' FROM visits LIMIT 1"),
    ("SELECT * FROM generate_series(1, 10) g", "WITH s AS (SELECT * FROM generate_series(1, 5) g) SELECT * FROM s"),
    ("SELECT id FROM t WHERE id IN (SELECT custom_fn(x) FROM u)", "SELECT t.id FROM t JOIN u ON t.id = custom_fn(u.x)"),
])
def test_a2_rewrite_accepted(original, rewrite):
    assert db.check_rewrite(original, rewrite) is None


@pytest.mark.parametrize("rewrite,name", [
    ("SELECT set_config('transaction_read_only', 'off', true)", "set_config"),
    ("SELECT pg_terminate_backend(pid) FROM pg_stat_activity", "pg_terminate_backend"),
    ("SELECT pg_sleep(100)", "pg_sleep"),
    ("SELECT PG_SLEEP(1)", "pg_sleep"),
    ("SELECT dblink_exec('host=x', 'DELETE FROM clinics')", "dblink_exec"),
    ("SELECT nextval('sargable_probe_seq')", "nextval"),
    ("SELECT lo_import('/etc/passwd')", "lo_import"),
    ("SELECT pg_read_file('/etc/passwd')", "pg_read_file"),
    ("SELECT query_to_xml('DELETE FROM clinics RETURNING 1', true, true, '')", "query_to_xml"),
    ("SELECT evil.count(*) FROM visits", "count"),
    ("SELECT public.lower(reason) FROM visits", "lower"),
    ("SELECT pg_catalog.pg_sleep(1)", "pg_sleep"),
    ("SELECT * FROM visits WHERE id = (SELECT max(id) FROM visits WHERE pg_sleep(1) IS NOT NULL)", "pg_sleep"),
    ("SELECT * FROM visits WHERE animal_id IN (SELECT animal_id FROM visits WHERE set_config('a.b', 'c', false) = 'c')",
     "set_config"),
    ("WITH x AS (SELECT pg_read_file('/etc/passwd') AS f) SELECT * FROM x", "pg_read_file"),
    ("SELECT row_number() OVER (ORDER BY pg_sleep(1)) FROM visits", "pg_sleep"),
    ("SELECT sum(fee) OVER w FROM visits WINDOW w AS (PARTITION BY set_config('a.b', 'c', false))", "set_config"),
    ("SELECT count(*) FILTER (WHERE pg_sleep(1) IS NULL) FROM visits", "pg_sleep"),
    ("SELECT * FROM visits v, LATERAL (SELECT pg_sleep(1)) s", "pg_sleep"),
    ("SELECT * FROM pg_ls_dir('.') AS f", "pg_ls_dir"),
    ("SELECT * FROM generate_series(1, 10) g", "generate_series"),
    ("SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)", "dblink"),
    ("SELECT animal_id FROM visits GROUP BY animal_id HAVING max(id) > pg_backend_pid()", "pg_backend_pid"),
    ("SELECT count(pg_sleep(1)) FROM visits", "pg_sleep"),
    ("SELECT id FROM visits ORDER BY random()", "random"),
    ("SELECT array_agg(id) FROM visits", "array_agg"),
    ("SELECT string_agg(reason, ',') FROM visits", "string_agg"),
    ("SELECT count(*) FROM clinics WHERE (SELECT (s.x).pg_terminate_backend IS NULL FROM (SELECT pg_backend_pid() AS x) s)",
     "pg_backend_pid"),
])
def test_a2_rewrite_with_new_function_rejected_naming_it(rewrite, name):
    with pytest.raises(ValueError) as e:
        db.check_rewrite(ORIGINAL, rewrite)
    assert name in str(e.value).lower()


@pytest.mark.parametrize("original,rewrite", [
    ("SELECT 'pg_sleep(1)' FROM visits -- pg_sleep(2)", "SELECT pg_sleep(1) FROM visits"),
    ("SELECT my.fn(id) FROM t", "SELECT fn(id) FROM t"),
    ("SELECT fn(id) FROM t", "SELECT evil.fn(id) FROM t"),
    ("SELECT fn(id) FROM t", "SELECT fn(id), other_fn(id) FROM t"),
])
def test_a2_original_only_licenses_identical_dotted_names(original, rewrite):
    with pytest.raises(ValueError):
        db.check_rewrite(original, rewrite)


@pytest.mark.parametrize("original,rewrite", [(ORIGINAL, "SELEC 1"), ("SELEC 1", "SELECT 1")])
def test_a2_unparseable_sql_rejected(original, rewrite):
    with pytest.raises(ValueError):
        db.check_rewrite(original, rewrite)
