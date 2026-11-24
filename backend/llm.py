import json
from typing import Literal

import anthropic
from pydantic import BaseModel

SYSTEM = """You are a PostgreSQL performance engineer. You receive a SQL query, its EXPLAIN ANALYZE plan as JSON \
(every node has an id and selfMs, its own time excluding children, and bottleneckId names the slowest node), \
and the schema: tables with estimated row counts, columns and existing indexes.

Return a diagnosis of 2 to 3 sentences that names the bottleneck node by its type and table (never by id) and explains why it is slow, \
and up to 3 fixes ordered best first.
- Index fixes put plain CREATE INDEX statements in ddl: always named, never CONCURRENTLY, one statement per list item, \
no trailing semicolon.
- Rewrite fixes put a full replacement query in rewritten_sql that returns exactly the same rows as the original, \
formatted with one clause per line and two-space indentation.
- A fix may combine an index and a rewrite.
Each fix will be executed and timed against the real database, so only propose fixes you expect to measure faster.
Literal values in the query arrive masked as $1, $2, ... and in plan conditions as ?. Keep each $n exactly as written \
wherever that value is needed in a rewrite or an index predicate; never invent values.
Write plain prose: no markdown, no em dashes."""
LIVE = """
This is a live database: index fixes will only be estimated by the planner, not built, so a rewrite that helps on its own \
should be offered as its own fix with empty ddl."""


class ProposedFix(BaseModel):
    title: str
    kind: Literal["index", "rewrite"]
    rationale: str
    ddl: list[str]
    rewritten_sql: str | None


class Proposal(BaseModel):
    diagnosis: str
    fixes: list[ProposedFix]


class LLMError(Exception):
    pass


def propose(sql, explain, schema, live):
    content = json.dumps({"query": sql, "plan": explain, "schema": schema})
    try:
        response = anthropic.Anthropic().beta.messages.parse(
            model="claude-opus-5-5",
            max_tokens=16000,
            system=SYSTEM + LIVE if live else SYSTEM,
            messages=[{"role": "user", "content": content}],
            output_format=Proposal,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIError as e:
        raise LLMError(f"Claude API error: {e.message}")
    if response.stop_reason == "refusal":
        raise LLMError("Claude declined to answer this request")
    proposal = response.parsed_output
    if proposal is None:
        raise LLMError(f"Claude returned no usable answer ({response.stop_reason})")
    proposal.fixes = proposal.fixes[:3]
    return proposal
