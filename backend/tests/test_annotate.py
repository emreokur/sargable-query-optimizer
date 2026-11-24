import json
from pathlib import Path

import pytest

import db

PLANS = json.loads((Path(__file__).parent / "plans.json").read_text())


def preorder(node):
    yield node
    for child in node["children"]:
        yield from preorder(child)


def flat(plan):
    return [{k: v for k, v in n.items() if k != "children"} for n in preorder(plan)]


def test_f1_loops_self_time_detail_and_optional_keys():
    plan, bottleneck = db.annotate(PLANS["sort_nested_loop"])
    nodes = flat(plan)
    expected = [
        {"id": 0, "type": "Limit", "totalMs": 60.0, "selfMs": 1.0, "rows": 10, "planRows": 10, "loops": 1},
        {"id": 1, "type": "Sort", "detail": "v.visited_at DESC, v.id",
         "totalMs": 59.0, "selfMs": 4.0, "rows": 10, "planRows": 10, "loops": 1},
        {"id": 2, "type": "Nested Loop", "detail": "(v.clinic_id = c.id)",
         "totalMs": 55.0, "selfMs": 18.5, "rows": 301, "planRows": 50, "loops": 1},
        {"id": 3, "type": "Index Scan", "relation": "clinics", "index": "clinics_pkey",
         "detail": "(city = 'Oslo'::text)", "totalMs": 0.5, "selfMs": 0.5,
         "rows": 3, "planRows": 3, "loops": 1, "rowsRemoved": 197},
        {"id": 4, "type": "Seq Scan", "relation": "visits", "detail": "(status = 'open'::text)",
         "totalMs": 36.0, "selfMs": 36.0, "rows": 301, "planRows": 120, "loops": 3, "rowsRemoved": 4498},
    ]
    assert len(nodes) == len(expected)
    for got, want in zip(nodes, expected):
        assert got.keys() == want.keys()
        assert got == pytest.approx(want)
        assert all(type(got[k]) is int for k in ("id", "rows", "planRows", "loops", "rowsRemoved") if k in got)
    assert [len(n["children"]) for n in preorder(plan)] == [1, 1, 2, 0, 0]
    assert bottleneck == 4


def test_f1_tie_goes_to_first_in_preorder_and_self_time_never_negative():
    plan, bottleneck = db.annotate(PLANS["tie"])
    nodes = list(preorder(plan))
    assert [n["id"] for n in nodes] == [0, 1, 2, 3]
    assert [n["selfMs"] for n in nodes] == pytest.approx([1.8, 4.2, 0.0, 4.2])
    assert nodes[0]["detail"] == "(a.owner_id = o.id)"
    assert "detail" not in nodes[2] and "relation" not in nodes[2]
    assert bottleneck == 1


def test_f1_zero_time_leaf():
    plan, bottleneck = db.annotate(PLANS["zero"])
    assert plan == {"id": 0, "type": "Result", "totalMs": 0.0, "selfMs": 0.0,
                    "rows": 1, "planRows": 1, "loops": 1, "children": []}
    assert bottleneck == 0
