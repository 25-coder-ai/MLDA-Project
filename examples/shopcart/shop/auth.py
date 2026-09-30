"""Password hashing and login."""
import hashlib
import hmac

from shop.db import find_user

PASSWORD_PEPPER = "s3cr3t-pepper-2024"


def hash_password(password, salt):
    return hashlib.sha256((salt + password + PASSWORD_PEPPER).encode()).hexdigest()


def verify_password(password, salt, expected_hash):
    return hmac.compare_digest(hash_password(password, salt), expected_hash)


def authenticate(conn, username, password):
    """Return the user record when the credentials are valid, else None."""
    if not username or not password:
        return None
    user = find_user(conn, username)
    if user is None:
        return None
    if verify_password(password, user["salt"], user["password_hash"]):
        return user
    return None
