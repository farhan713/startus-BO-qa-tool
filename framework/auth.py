"""User accounts + session auth for the Stratus QA Tool.

Per-laptop local auth — there is no shared server. The point is privacy
(your scenarios + run history aren't visible to whoever else might use the
laptop) and identity (so scenarios are stamped with an owner that survives
when scenarios.json is synced via git).

Stored at `knowledge_base/users.json`:
  {
    "version": 1,
    "users": {
      "farhan": {
        "password_hash": "<werkzeug-hash>",
        "role": "admin",
        "created_ts": 1780999999,
        "last_login_ts": 1781000123
      },
      ...
    }
  }

Bootstrap rule:  no users exist → the first POST /api/auth/register creates
an admin (no auth required). After that, only admins may register new users.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from functools import wraps
from pathlib import Path

from flask import jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash


# ---------- Storage ----------

def _store_path() -> Path:
    if env := os.environ.get("STRATUS_USERS_PATH"):
        return Path(env)
    return Path(__file__).resolve().parent.parent / "knowledge_base" / "users.json"


def _read() -> dict:
    p = _store_path()
    if not p.exists():
        return {"version": 1, "users": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "users": {}}


def _write(data: dict) -> None:
    p = _store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, p)


# ---------- Public API ----------

def has_users() -> bool:
    return bool((_read().get("users") or {}))


def get_user(username: str) -> dict | None:
    return (_read().get("users") or {}).get(username)


def list_users() -> list[dict]:
    """Public list — never includes password hashes."""
    out = []
    for name, u in (_read().get("users") or {}).items():
        out.append({
            "username": name,
            "role": u.get("role", "user"),
            "created_ts": u.get("created_ts"),
            "last_login_ts": u.get("last_login_ts"),
        })
    out.sort(key=lambda u: u.get("created_ts") or 0)
    return out


def create_user(username: str, password: str, role: str = "user") -> None:
    """Raises ValueError on bad input or collision."""
    username = (username or "").strip().lower()
    if not username or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("username must be alphanumeric / dashes / underscores")
    if len(username) > 40:
        raise ValueError("username too long")
    if not password or len(password) < 4:
        raise ValueError("password must be at least 4 characters")
    if role not in ("admin", "user"):
        raise ValueError("role must be 'admin' or 'user'")
    data = _read()
    bucket = data.setdefault("users", {})
    if username in bucket:
        raise ValueError(f"user {username!r} already exists")
    bucket[username] = {
        "password_hash": generate_password_hash(password),
        "role": role,
        "created_ts": time.time(),
        "last_login_ts": None,
    }
    _write(data)


def verify_password(username: str, password: str) -> bool:
    u = get_user((username or "").strip().lower())
    if not u: return False
    return check_password_hash(u.get("password_hash") or "", password)


def stamp_login(username: str) -> None:
    data = _read()
    if username in (data.get("users") or {}):
        data["users"][username]["last_login_ts"] = time.time()
        _write(data)


def change_password(username: str, new_password: str) -> None:
    if not new_password or len(new_password) < 4:
        raise ValueError("password must be at least 4 characters")
    data = _read()
    u = (data.get("users") or {}).get(username)
    if not u:
        raise ValueError("user not found")
    u["password_hash"] = generate_password_hash(new_password)
    _write(data)


def delete_user(username: str) -> bool:
    data = _read()
    bucket = data.get("users") or {}
    if username not in bucket: return False
    del bucket[username]
    _write(data)
    return True


# ---------- Flask decorators ----------

def current_user() -> str | None:
    """The logged-in username on this request, or None."""
    return session.get("username")


def current_role() -> str | None:
    name = current_user()
    if not name: return None
    u = get_user(name)
    return (u or {}).get("role")


def login_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return jsonify({"error": "login required"}), 401
        return fn(*a, **kw)
    return wrapper


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not current_user():
            return jsonify({"error": "login required"}), 401
        if current_role() != "admin":
            return jsonify({"error": "admin only"}), 403
        return fn(*a, **kw)
    return wrapper


# ---------- Session secret ----------

def session_secret() -> str:
    """Stable per-laptop secret so sessions survive Flask restarts."""
    sec_path = Path(__file__).resolve().parent.parent / "knowledge_base" / ".session_secret"
    if sec_path.exists():
        try: return sec_path.read_text().strip()
        except Exception: pass
    secret = secrets.token_hex(32)
    sec_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        sec_path.write_text(secret)
        os.chmod(sec_path, 0o600)
    except Exception: pass
    return secret
