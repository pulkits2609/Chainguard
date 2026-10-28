"""Accounts and expiring browser sessions. Passwords and session IDs are never stored raw."""

import hashlib
import hmac
import re
import secrets
import time
import uuid

import psycopg
from psycopg.rows import dict_row
from settings import require_chainguard_database


SESSION_SECONDS = 12 * 60 * 60


def token_hash(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_credentials(username, password):
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_-]{3,32}", username):
        raise ValueError("Username must have 3–32 letters, numbers, underscores or hyphens")
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters")


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt),
                            n=16384, r=8, p=1, dklen=32, maxmem=64 * 1024 * 1024)
    return "scrypt$" + salt + "$" + digest.hex()


def password_matches(password, stored):
    try:
        method, salt, _ = stored.split("$")
        return method == "scrypt" and hmac.compare_digest(password_hash(password, salt), stored)
    except (ValueError, TypeError):
        return False


class Accounts:
    def __init__(self, database_url):
        require_chainguard_database(database_url)
        self.database_url = database_url
        # Unknown users still perform one password calculation during login.
        self.dummy_password = password_hash(secrets.token_urlsafe(24))
        with self.connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL, is_admin BOOLEAN NOT NULL DEFAULT FALSE)""")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS single_admin ON users (is_admin) WHERE is_admin")
            connection.execute("""CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL, expires_at BIGINT NOT NULL)""")

    def connect(self):
        return psycopg.connect(self.database_url, row_factory=dict_row, connect_timeout=5)

    @staticmethod
    def public_user(row):
        return {key: row[key] for key in ("id", "username", "is_admin")}

    def register(self, username, password):
        check_credentials(username, password)
        user = {"id": str(uuid.uuid4()), "username": username.lower(), "is_admin": False}
        try:
            with self.connect() as connection:
                connection.execute("INSERT INTO users VALUES (%s, %s, %s, FALSE)",
                                   (user["id"], user["username"], password_hash(password)))
        except psycopg.errors.UniqueViolation:
            raise ValueError("This username is already taken") from None
        return user

    def initialize_admin(self, username, password):
        check_credentials(username, password)
        with self.connect() as connection:
            # Bootstrap may be run twice, but must never promote a normal account or reset a password.
            connection.execute("SELECT pg_advisory_xact_lock(834219)")
            existing = connection.execute("SELECT * FROM users WHERE is_admin").fetchone()
            if existing:
                if existing["username"] != username.lower():
                    raise ValueError("An administrator already exists; no account was changed")
                return self.public_user(existing), False
            user = {"id": str(uuid.uuid4()), "username": username.lower(), "is_admin": True}
            try:
                connection.execute("INSERT INTO users VALUES (%s, %s, %s, TRUE)",
                                   (user["id"], user["username"], password_hash(password)))
            except psycopg.errors.UniqueViolation:
                raise ValueError("That username already belongs to an account; no account was changed") from None
            return user, True

    def find_admin(self, username):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username=%s AND is_admin", (username.lower(),)).fetchone()
            if not row:
                raise ValueError("Initialize the configured administrator first")
            return self.public_user(row)

    def login(self, username, password):
        if not isinstance(username, str) or not isinstance(password, str) or len(username) > 32 or len(password) > 128:
            raise ValueError("Incorrect username or password")
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username=%s", (username.lower(),)).fetchone()
        stored = row["password_hash"] if row else self.dummy_password
        valid = password_matches(password, stored)
        if not row or not valid:
            raise ValueError("Incorrect username or password")
        return self.public_user(row)

    def new_session(self, user_id):
        token = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires = int(time.time()) + SESSION_SECONDS
        with self.connect() as connection:
            connection.execute("DELETE FROM sessions WHERE expires_at < %s", (int(time.time()),))
            connection.execute("INSERT INTO sessions VALUES (%s, %s, %s, %s)",
                               (token_hash(token), user_id, csrf, expires))
        return token, csrf

    def session(self, token):
        if not token or len(token) > 128:
            return None
        with self.connect() as connection:
            row = connection.execute("""SELECT users.id, users.username, users.is_admin, sessions.csrf_token
                FROM sessions JOIN users ON users.id=sessions.user_id
                WHERE sessions.token_hash=%s AND sessions.expires_at > %s""",
                                     (token_hash(token), int(time.time()))).fetchone()
        return row

    def logout(self, token):
        if token and len(token) <= 128:
            with self.connect() as connection:
                connection.execute("DELETE FROM sessions WHERE token_hash=%s", (token_hash(token),))
