import re

import pglast

CONSTANTS = {"SCONST", "ICONST", "FCONST", "BCONST", "XCONST", "USCONST"}
COMMENTS = {"SQL_COMMENT", "C_COMMENT"}


def _replace(sql, replacement):
    out, pos = [], 0
    for tok in pglast.scan(sql):
        new = replacement(tok.name, sql[tok.start:tok.end + 1])
        if new is not None:
            out.append(sql[pos:tok.start] + new)
            pos = tok.end + 1
    return "".join(out) + sql[pos:]


def mask_sql(sql):
    if "\x00" in sql:
        raise ValueError("The query contains a NUL byte.")
    values = []

    def replacement(name, text):
        if name in CONSTANTS:
            if text not in values:
                values.append(text)
            return f"${values.index(text) + 1}"
        if name in COMMENTS:
            return " "
        if any(a == "$" and b.isdigit() for a, b in zip(text, text[1:])):
            raise ValueError("The query already contains $n placeholders or identifiers, which cannot be masked.")
        return None

    try:
        return _replace(sql, replacement), values
    except pglast.parser.ParseError as e:
        raise ValueError(f"The query cannot be scanned: {e}")


def mask_text(text):
    if "\x00" in text:
        return "?"
    try:
        return _replace(text, lambda name, _: "?" if name in CONSTANTS else None)
    except pglast.parser.ParseError:
        return "?"


def unmask(text, values):
    def restore(m):
        n = int(m[1])
        return values[n - 1] if 1 <= n <= len(values) else m[0]

    return re.sub(r"\$(\d+)", restore, text)
