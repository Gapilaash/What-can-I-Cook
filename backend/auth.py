"""
Lightweight self-hosted auth: username/password with salted PBKDF2 hashes
and random session tokens stored in SQLite. No external auth service, no
cost, no third-party dependency (uses only Python's built-in hashlib/secrets).
"""
import hashlib
import re
import secrets
from datetime import datetime, timedelta

from fastapi import Cookie, HTTPException

from database import get_conn

SESSION_DAYS = 30
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(password, salt), stored)


def create_user(username: str, password: str, email: str | None = None) -> int:
    username = username.strip().lower()
    email = (email or "").strip().lower() or None
    if len(username) < 3:
        raise HTTPException(400, "Username must be at least 3 characters")
    if len(password) < 4:
        raise HTTPException(400, "Password must be at least 4 characters")
    if email and not EMAIL_RE.match(email):
        raise HTTPException(400, "That email address doesn't look valid")
    with get_conn() as conn:
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
        if existing:
            raise HTTPException(409, "That username is already taken")
        if email:
            existing_email = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if existing_email:
                raise HTTPException(409, "An account with that email already exists — try logging in instead")
        row = conn.execute(
            "INSERT INTO users (username, password_hash, email) VALUES (?, ?, ?) RETURNING id",
            (username, hash_password(password), email),
        ).fetchone()
        conn.commit()
        return row["id"]


def _unique_username_from_email(conn, email: str) -> str:
    """Turn 'anna.chef@gmail.com' into a free username like 'anna.chef',
    adding a numeric suffix if that's already taken."""
    base = (email.split("@")[0] or "cook").strip().lower()
    base = "".join(c for c in base if c.isalnum() or c in "._-") or "cook"
    if len(base) < 3:
        base = (base + "cook")[:3]
    candidate = base
    n = 1
    while conn.execute("SELECT id FROM users WHERE username = ?", (candidate,)).fetchone():
        n += 1
        candidate = f"{base}{n}"
    return candidate


def start_email_verification(user_id: int) -> str | None:
    """(Re)generate a verification token for this user's email.
    Returns the token, or None if the account has no email to verify."""
    with get_conn() as conn:
        row = conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row or not row["email"]:
            return None
        token = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE users SET verification_token = ?, verification_sent_at = ? WHERE id = ?",
            (token, datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()
        return token


def verify_email_token(token: str) -> bool:
    """Marks the matching account's email as verified. Tokens expire after 24h."""
    if not token:
        return False
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, verification_sent_at FROM users WHERE verification_token = ?", (token,)
        ).fetchone()
        if not row:
            return False
        try:
            sent = datetime.fromisoformat(row["verification_sent_at"])
            if datetime.utcnow() - sent > timedelta(hours=24):
                return False
        except (TypeError, ValueError):
            pass  # no timestamp on old rows - don't block verification over it
        conn.execute(
            "UPDATE users SET email_verified = 1, verification_token = NULL WHERE id = ?",
            (row["id"],),
        )
        conn.commit()
        return True


def find_or_create_google_user(google_id: str, email: str, name: str, picture: str) -> int:
    """Log in an existing Google-linked (or email-matching) account, or create one.
    Returns the user id."""
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM users WHERE google_id = ?", (google_id,)).fetchone()
        if row:
            return row["id"]

        # Someone who signed up with username/password using the same email:
        # link this Google identity to that existing account instead of duplicating it.
        if email:
            row = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET google_id = ?, name = COALESCE(name, ?), "
                    "avatar_url = COALESCE(avatar_url, ?), email_verified = 1 WHERE id = ?",
                    (google_id, name, picture, row["id"]),
                )
                conn.commit()
                return row["id"]

        username = _unique_username_from_email(conn, email or google_id)
        # No password for Google-only accounts; store an unusable random hash
        # (password_hash stays NOT NULL, but nothing hashes to it).
        unusable = hash_password(secrets.token_hex(32))
        row = conn.execute(
            "INSERT INTO users (username, password_hash, email, name, avatar_url, "
            "auth_provider, google_id, plan, email_verified) VALUES (?, ?, ?, ?, ?, 'google', ?, 'free', 1) "
            "RETURNING id",
            (username, unusable, email, name, picture, google_id),
        ).fetchone()
        conn.commit()
        return row["id"]


def get_profile(user_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, username, email, name, avatar_url, auth_provider, plan, "
            "plan_updated_at, email_verified, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "Account not found")
    profile = dict(row)
    profile["email_verified"] = bool(profile.get("email_verified"))
    return profile


def update_profile(user_id: int, name: str | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise HTTPException(400, "Name can't be empty")
    if len(name) > 80:
        raise HTTPException(400, "Name is too long")
    with get_conn() as conn:
        conn.execute("UPDATE users SET name = ? WHERE id = ?", (name, user_id))
        conn.commit()
    return get_profile(user_id)


def set_plan(user_id: int, plan: str) -> dict:
    """Mock plan switch - no payment provider involved. Free demo/testing only."""
    if plan not in ("free", "pro"):
        raise HTTPException(400, "Plan must be 'free' or 'pro'")
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET plan = ?, plan_updated_at = ? WHERE id = ?",
            (plan, datetime.utcnow().isoformat(), user_id),
        )
        conn.commit()
    return get_profile(user_id)


# ------------------------------------------------------------------
# Free vs Pro: daily AI usage quota
# ------------------------------------------------------------------
FREE_AI_DAILY_LIMIT = 5  # ingredient substitutions + freestyle/AI recipes, combined, per day


def check_and_use_ai_quota(user_id: int) -> dict:
    """Free plan: FREE_AI_DAILY_LIMIT AI calls/day (substitutions + freestyle
    recipes combined). Pro plan: unlimited. Call this right before doing the
    (costly) AI call. Raises HTTPException(402) once a free user is over the
    limit for today. Returns {"plan", "used", "limit"} ("limit" is None for pro)."""
    with get_conn() as conn:
        row = conn.execute("SELECT plan FROM users WHERE id = ?", (user_id,)).fetchone()
    plan = (row["plan"] if row else "free") or "free"
    if plan == "pro":
        return {"plan": "pro", "used": None, "limit": None}

    today = datetime.utcnow().date().isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ai_usage (user_id, usage_date, count) VALUES (?, ?, 0) "
            "ON CONFLICT (user_id, usage_date) DO NOTHING",
            (user_id, today),
        )
        conn.commit()
        used = conn.execute(
            "SELECT count FROM ai_usage WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        ).fetchone()["count"]

        if used >= FREE_AI_DAILY_LIMIT:
            raise HTTPException(
                402,
                f"You've used today's {FREE_AI_DAILY_LIMIT} free AI requests. "
                "Upgrade to Pro for unlimited AI recipes & substitutions.",
            )

        conn.execute(
            "UPDATE ai_usage SET count = count + 1 WHERE user_id = ? AND usage_date = ?",
            (user_id, today),
        )
        conn.commit()
    return {"plan": "free", "used": used + 1, "limit": FREE_AI_DAILY_LIMIT}


def authenticate(identifier: str, password: str) -> int:
    """Log in with either a username or an email address in `identifier`."""
    identifier = identifier.strip().lower()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ? OR email = ?",
            (identifier, identifier),
        ).fetchone()
        if not row or not verify_password(password, row["password_hash"]):
            raise HTTPException(401, "Invalid username/email or password")
        return row["id"]


def create_session(user_id: int) -> str:
    token = secrets.token_hex(32)
    expires = (datetime.utcnow() + timedelta(days=SESSION_DAYS)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
            (token, user_id, expires),
        )
        conn.commit()
    return token


def destroy_session(token: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()


def get_current_user(session_token: str | None = Cookie(default=None)) -> dict:
    """FastAPI dependency: returns {'id':.., 'username':..} or raises 401."""
    if not session_token:
        raise HTTPException(401, "Not logged in")
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT u.id, u.username, s.expires_at FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token = ?
            """,
            (session_token,),
        ).fetchone()
    if not row:
        raise HTTPException(401, "Session expired, please log in again")
    if datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        destroy_session(session_token)
        raise HTTPException(401, "Session expired, please log in again")
    return {"id": row["id"], "username": row["username"]}


def get_current_user_optional(session_token: str | None = Cookie(default=None)) -> dict | None:
    """Same as above but returns None instead of raising, for optional-auth endpoints."""
    try:
        return get_current_user(session_token)
    except HTTPException:
        return None
