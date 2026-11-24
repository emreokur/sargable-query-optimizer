import re
from pathlib import Path

import pglast
import pytest

import mask

CONSTANTS = {"SCONST", "ICONST", "FCONST", "BCONST", "XCONST", "USCONST"}
SAMPLES = re.findall(r"sql: `(.*?)`", (Path(__file__).parents[2] / "frontend/src/samples.ts").read_text(), re.S)
REALISTIC = SAMPLES + [
    "SELECT * FROM owners WHERE email = 'O''Brien@example.com' AND created_at > now() - interval '30 days'",
    r"SELECT E'line\nbreak\'s', $q$it's $1 here$q$, U&'d\0061t', B'1010', X'FF', -3.5e-2 FROM t1 WHERE col2 IN (1, 2, 1)",
    'SELECT "Quoted 1", t1.col2 FROM t1 /* lookup 99 */ WHERE t1.col2 = 99 -- trailing 99\nLIMIT 10 OFFSET 20',
    "SELECT '$2', 'x', $$ $1 $$, '$3'",
    "SELECT date '2024-02-29' + 1, 1.0::numeric(5,1), 'a' || 'b', N'nat', 1_000, 0x1F, .5, 1.",
    "SELECT a/* x */FROM t/**/WHERE b = 'c'--tail\n",
    "SELECT U&'d!0061t' UESCAPE '!', ARRAY[1, 2, 3], '{1,2}'::int[] FROM t WHERE ts BETWEEN '2026-01-01' AND '2026-02-01'",
]


def space_comments(sql):
    out, pos = [], 0
    for tok in pglast.scan(sql):
        if tok.name in ("SQL_COMMENT", "C_COMMENT"):
            out.append(sql[pos:tok.start] + " ")
            pos = tok.end + 1
    return "".join(out) + sql[pos:]


@pytest.mark.parametrize("sql,masked,values", [
    ("SELECT 'it''s'", "SELECT $1", ["'it''s'"]),
    (r"SELECT E'a\'b', e'\\'", "SELECT $1, $2", [r"E'a\'b'", r"e'\\'"]),
    ("SELECT $$x$$, $tag$it's$tag$", "SELECT $1, $2", ["$$x$$", "$tag$it's$tag$"]),
    (r"SELECT U&'d\0061t'", "SELECT $1", [r"U&'d\0061t'"]),
    ("SELECT B'101', X'1F', b'0'", "SELECT $1, $2, $3", ["B'101'", "X'1F'", "b'0'"]),
    ("SELECT 42, 3.5, 1e10, .5, 1.5e-3, 0x1F", "SELECT $1, $2, $3, $4, $5, $6", ["42", "3.5", "1e10", ".5", "1.5e-3", "0x1F"]),
    ("SELECT -7, - 8", "SELECT -$1, - $2", ["7", "8"]),
    ("SELECT interval '1 year', date '2020-01-01', '5'::int",
     "SELECT interval $1, date $2, $3::int", ["'1 year'", "'2020-01-01'", "'5'"]),
    ("SELECT 'a', 1, 'a', '1', 1", "SELECT $1, $2, $1, $3, $2", ["'a'", "1", "'1'"]),
    ("SELECT a -- 99 bottles\nFROM t /* 'x' */ WHERE b = 2", "SELECT a  \nFROM t   WHERE b = $1", ["2"]),
    ("SELECT a/**/FROM t", "SELECT a FROM t", []),
    ("SELECT 1/* a /* nested */ b */+2", "SELECT $1 +$2", ["1", "2"]),
    ("SELECT x -- $1 in a comment", "SELECT x  ", []),
    ("SELECT $tag$ $1 $tag$, a$b FROM t", "SELECT $1, a$b FROM t", ["$tag$ $1 $tag$"]),
    ('SELECT t1.col2, "Quoted 1", "it\'s" FROM t1 WHERE col2 > 0', 'SELECT t1.col2, "Quoted 1", "it\'s" FROM t1 WHERE col2 > $1', ["0"]),
    ("SELECT 'SELECT', '$1' FROM t LIMIT 5", "SELECT $1, $2 FROM t LIMIT $3", ["'SELECT'", "'$1'", "5"]),
    ("SELECT id FROM t", "SELECT id FROM t", []),
])
def test_s9_mask_sql_cases(sql, masked, values):
    assert mask.mask_sql(sql) == (masked, values)


@pytest.mark.parametrize("sql", [
    "SELECT * FROM t WHERE id = $1", "SELECT $2", "SELECT col$1 FROM t", "SELECT t.a$2b FROM t",
    'SELECT "x$3" FROM t', "SELECT 1 FROM t AS t$1", "SELECT 'unterminated", "SELECT $$open",
])
def test_s9_mask_sql_rejects_params_dollar_digit_identifiers_and_unscannable(sql):
    with pytest.raises(ValueError):
        mask.mask_sql(sql)


@pytest.mark.parametrize("sql", REALISTIC)
def test_s9_round_trip_and_no_literal_left(sql):
    masked, values = mask.mask_sql(sql)
    assert mask.unmask(masked, values) == space_comments(sql)
    tokens = pglast.scan(masked)
    assert not [t for t in tokens if t.name in CONSTANTS | {"SQL_COMMENT", "C_COMMENT"}]
    assert set(re.findall(r"\$(\d+)", masked)) == {str(n) for n in range(1, len(values) + 1)}
    assert len(values) == len(set(values))
    for v in values:
        if not v.isdigit():
            assert v not in masked


def test_s9_samples_were_read():
    assert len(SAMPLES) >= 9


@pytest.mark.parametrize("text,masked", [
    ("(status = 'open'::text)", "(status = ?::text)"),
    ("((animal_id = 4242) AND (fee > 10.5))", "((animal_id = ?) AND (fee > ?))"),
    ("(email ~~ '%@x.com'::text)", "(email ~~ ?::text)"),
    ("(visited_at >= '2026-03-02 00:00:00+00'::timestamp with time zone)", "(visited_at >= ?::timestamp with time zone)"),
    ("(x = ANY ('{1,2,3}'::integer[]))", "(x = ANY (?::integer[]))"),
    ("(lower(email) = 'O''Brien'::text)", "(lower(email) = ?::text)"),
    ("(v.animal_id = a.id)", "(v.animal_id = a.id)"),
    ("v.visited_at DESC, v.id", "v.visited_at DESC, v.id"),
    ("(name = 'unterminated", "?"),
])
def test_s9_mask_text(text, masked):
    assert mask.mask_text(text) == masked


@pytest.mark.parametrize("text,values,out", [
    ("a $1 b $2", ["x", "y"], "a x b y"),
    ("$10 and $1", ["v1"], "$10 and v1"),
    ("$10 $1 $11", [f"v{i}" for i in range(1, 11)], "v10 v1 $11"),
    ("$0 $3 $1", ["a", "b"], "$0 $3 a"),
    ("$1$1, costs $$ and $", ["a"], "aa, costs $$ and $"),
    ("$1 $2", ["'$2'", "z"], "'$2' z"),
    ("$1 $2", ["'$$1'", "$1"], "'$$1' $1"),
    ("Filter on $1.", ["'open'"], "Filter on 'open'."),
    ("$1", [], "$1"),
])
def test_s9_unmask(text, values, out):
    assert mask.unmask(text, values) == out


@pytest.mark.parametrize("sql", ["SELECT * FROM owners WHERE email = 'a@b.c'\x00 OR email = 'secret' /* note */",
                                 "SELECT 1\x00", "\x00SELECT 'x'"])
def test_b3_mask_sql_rejects_nul(sql):
    with pytest.raises(ValueError):
        mask.mask_sql(sql)


@pytest.mark.parametrize("text", ["(email = 'a'::text)\x00 OR (email = 'secret'::text)", "\x00", "(id = 1)\x00"])
def test_b3_mask_text_fails_closed_on_nul(text):
    assert mask.mask_text(text) == "?"
