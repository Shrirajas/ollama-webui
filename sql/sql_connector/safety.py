"""Which SQL the model may run: one statement that only reads.

This is the first of three locks. The others are on the connection itself:
SQLite is opened read-only, and PostgreSQL and MySQL run every query in a
read-only transaction (see `connectors.py`). A database user that can only
read is the fourth, and the one to rely on: set it up on the server.

AnythingLLM only asks its model, in the tool's description, to send SELECTs.
Here the request is refused before it reaches the database.
"""

from __future__ import annotations

import re


class UnsafeQuery(ValueError):
    """The statement could change the database, or is not one statement."""


# A statement may start with these. SHOW, DESCRIBE and EXPLAIN are MySQL's ways to look around.
_STARTS = {"select", "with", "show", "describe", "desc", "explain", "values", "table"}

# Words that write, lock, wait, or reach outside the database from inside a
# statement that starts like a read. Checked anywhere outside strings and
# comments, so that a write hidden in a CTE (`WITH x AS (DELETE ...)`) or
# behind EXPLAIN ANALYZE is caught. Statements that start with a write
# (DROP, SET, ATTACH, PRAGMA ...) are already refused by `_STARTS`; the list
# stays short so that ordinary column names (owner, comment, status) pass.
_FORBIDDEN = {
    "insert", "update", "delete", "merge", "upsert", "truncate", "create", "alter", "drop",
    "grant", "revoke", "copy", "call", "exec", "execute", "lock",
    "into",  # SELECT ... INTO makes a table (PostgreSQL), a variable or a file (MySQL)
    "outfile", "dumpfile", "load_file",
    "nextval", "setval", "set_config", "pg_sleep", "sleep", "benchmark",
    "dblink", "dblink_exec", "lo_import", "lo_export", "pg_read_file", "pg_read_binary_file",
    "pg_ls_dir", "pg_stat_file", "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_advisory_lock", "pg_advisory_xact_lock", "query_to_xml", "query_to_xml_and_xmlschema",
}

# Strings, quoted names and comments, removed before the words are checked.
_LITERALS = re.compile(
    r"""
      '(?:[^'\\]|\\.|'')*'          # 'string', with '' or \' inside
    | "(?:[^"]|"")*"                # "quoted name"
    | `(?:[^`]|``)*`                # `quoted name` (MySQL)
    | \$(\w*)\$.*?\$\1\$            # $$dollar string$$ (PostgreSQL)
    | --[^\n]*                      # -- comment
    | \#[^\n]*                      # # comment (MySQL)
    | /\*.*?\*/                     # /* comment */
    """,
    re.VERBOSE | re.DOTALL,
)
_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")


def check(sql: str) -> str:
    """Returns the statement without a trailing semicolon, or raises UnsafeQuery."""
    text = (sql or "").strip()
    if not text:
        raise UnsafeQuery("The query is empty.")
    if "/*!" in text:
        raise UnsafeQuery("MySQL's /*! ... */ comments run as code and are not allowed.")
    bare = _LITERALS.sub(" ", text)
    if "'" in bare or '"' in bare or "`" in bare or "/*" in bare:
        raise UnsafeQuery("The query has a string, name or comment that is not closed.")

    body = bare.strip().rstrip(";").strip()
    if ";" in body:
        raise UnsafeQuery("Only one statement can be run at a time.")
    words = [w.lower() for w in _WORD.findall(body)]
    if not words or words[0] not in _STARTS:
        raise UnsafeQuery("Only queries that read are allowed: SELECT, WITH, SHOW, DESCRIBE or EXPLAIN.")
    found = sorted(set(words) & _FORBIDDEN)
    if found:
        raise UnsafeQuery(f"Only queries that read are allowed; this one uses {', '.join(w.upper() for w in found)}.")
    return text.rstrip().rstrip(";").rstrip()
