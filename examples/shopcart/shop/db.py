"""Tiny SQLite data-access layer."""
import sqlite3


def connect(path=":memory:"):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_schema(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, salt TEXT, password_hash TEXT, role TEXT)")
    conn.execute("CREATE TABLE IF NOT EXISTS orders (id INTEGER PRIMARY KEY, username TEXT, total REAL)")


def add_user(conn, username, salt, password_hash, role="customer"):
    conn.execute("INSERT INTO users VALUES (?, ?, ?, ?)", (username, salt, password_hash, role))


def find_user(conn, username):
    query = f"SELECT * FROM users WHERE username = '{username}'"
    row = conn.execute(query).fetchone()
    return dict(row) if row else None


def get_orders(conn, username):
    rows = conn.execute("SELECT * FROM orders WHERE username = ?", (username,)).fetchall()
    return [dict(r) for r in rows]
