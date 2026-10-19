"""A check of the SQL connector without a model or a database server:  cd backend && python -m sql_connector"""

import asyncio
import json
import sqlite3
import tempfile
from pathlib import Path

from .connectors import Connection, DatabaseError, check_fields, connector
from .safety import UnsafeQuery, check
from .store import ConnectionStore
from .tools import SqlTools

READS = [
    "SELECT * FROM staff WHERE first_name LIKE 'sam%'",
    "select count(*) from orders;",
    "WITH t AS (SELECT 1 AS n) SELECT n FROM t",
    "SELECT 'drop table x; delete' AS note -- update in a comment",
    "SELECT owner, comment, status, replace(name, 'a', 'b') FROM pets",
    "EXPLAIN SELECT 1",
    "SHOW TABLES",
]
WRITES = [
    "DELETE FROM orders",
    "drop table orders",
    "SELECT 1; DELETE FROM orders",
    "WITH gone AS (DELETE FROM orders RETURNING *) SELECT * FROM gone",
    "EXPLAIN ANALYZE UPDATE orders SET total = 0",
    "SELECT * INTO backup FROM orders",
    "SELECT * FROM orders INTO OUTFILE '/tmp/x'",
    "SELECT pg_sleep(100)",
    "SELECT nextval('orders_id_seq')",
    "SELECT * FROM orders FOR UPDATE",
    "PRAGMA writable_schema = 1",
    "ATTACH DATABASE 'x.db' AS x",
    "/*!50000 DROP TABLE orders */ SELECT 1",
    "SELECT 'unclosed",
    "",
]


def _sample(folder: Path) -> Path:
    path = folder / "shop.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, total REAL, placed TEXT);
        INSERT INTO customers VALUES (1, 'Asha', 'Pune'), (2, 'Ravi', 'Mysuru'), (3, 'Meera', 'Pune');
        INSERT INTO orders VALUES (1, 1, 1200.5, '2026-09-01'), (2, 1, 300, '2026-09-12'), (3, 2, 999, '2026-10-02');
        CREATE VIEW totals AS SELECT customer_id, SUM(total) AS spent FROM orders GROUP BY customer_id;
        """
    )
    con.close()
    return path


def main() -> None:
    for sql in READS:
        check(sql)
    for sql in WRITES:
        try:
            check(sql)
        except UnsafeQuery:
            continue
        raise AssertionError(f"not refused: {sql!r}")

    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        path = _sample(folder)
        shop = Connection(id="shop", engine="sqlite", path=str(path))
        check_fields(shop)
        db = connector(shop, timeout=5)
        db.validate()
        assert [r[0] for r in db.list_tables().rows] == ["customers", "orders", "totals"]
        assert [r[0] for r in db.table_schema("customers").rows] == ["id", "name", "city"]
        try:
            db.table_schema("nope")
            raise AssertionError("a missing table was described")
        except DatabaseError:
            pass
        assert db.run("SELECT name FROM customers ORDER BY id", max_rows=2).truncated

        # The read-only lock on the connection holds even when the text check is skipped.
        try:
            db.run("DELETE FROM orders")
            raise AssertionError("SQLite let a write through")
        except DatabaseError:
            pass
        assert db.run("SELECT COUNT(*) FROM orders").rows == [[3]]

        # The tools, as the model calls them.
        tools = SqlTools([shop], max_rows=50, timeout=5)
        text, event = asyncio.run(tools.run("list_databases", {}))
        assert json.loads(text)["databases"][0]["database_id"] == "shop"
        text, event = asyncio.run(tools.run("list_tables", {"database_id": "shop"}))
        assert event["rows"] == 3
        text, event = asyncio.run(tools.run("get_table_schema", '{"database_id": "shop", "table_name": "orders"}'))
        assert "customer_id" in text
        text, event = asyncio.run(tools.run("query", {"sql_query": "SELECT c.city, SUM(o.total) AS spent FROM orders o "
                                                                     "JOIN customers c ON c.id = o.customer_id "
                                                                     "GROUP BY c.city ORDER BY spent DESC"}))
        assert json.loads(text)["rows"] == [["Pune", 1500.5], ["Mysuru", 999.0]] and event["sql"]
        text, event = asyncio.run(tools.run("query", {"database_id": "shop", "sql_query": "DROP TABLE orders"}))
        assert text.startswith("Error:") and event["error"]
        text, event = asyncio.run(tools.run("query", {"database_id": "other", "sql_query": "SELECT 1"}))
        assert "No database 'other'" in text
        assert "`shop`" in tools.system_prompt()

        # Saved connections: the password never reaches the page, and an edit without one keeps it.
        store = ConnectionStore(folder / "sql")
        saved = store.save(ConnectionStore.build({"name": "Sales DB", "engine": "postgresql", "host": "db",
                                                  "user": "reader", "password": "s3cret", "database": "sales"}))
        assert saved.id == "sales-db" and saved.public()["password"] is True
        assert (store.file.stat().st_mode & 0o777) == 0o600
        edited = ConnectionStore.build({"name": "Sales DB", "engine": "postgresql", "host": "db2",
                                        "user": "reader", "database": "sales"})
        store.save(edited, original_id="sales-db")
        assert store.get("sales-db").password == "s3cret" and store.get("sales-db").host == "db2"
        try:
            store.save(ConnectionStore.build({"name": "sales_db", "engine": "sqlite", "path": str(path)}))
            raise AssertionError("a second connection with the same id was saved")
        except DatabaseError:
            pass
        assert store.delete("sales-db") and store.all() == []

    print("SQL connector: query checks, SQLite, read-only lock, tools and saved connections all pass.")


if __name__ == "__main__":
    main()
