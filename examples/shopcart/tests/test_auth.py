from shop import db
from shop.auth import authenticate, hash_password


def make_conn():
    conn = db.connect()
    db.init_schema(conn)
    db.add_user(conn, "alice", "NaCl", hash_password("wonderland", "NaCl"))
    return conn


def test_authenticate_success():
    assert authenticate(make_conn(), "alice", "wonderland")["username"] == "alice"


def test_authenticate_wrong_password():
    assert authenticate(make_conn(), "alice", "nope") is None


def test_authenticate_unknown_user():
    assert authenticate(make_conn(), "bob", "x") is None


def test_authenticate_empty_password():
    assert authenticate(make_conn(), "alice", "") is None
