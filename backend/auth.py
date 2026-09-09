"""Authentication — PBKDF2 password hashing + HMAC-signed session cookies.

The same design as the server dashboard's `backend/auth.py`, so there is one
pattern to remember across both services. Stdlib only, no dependencies.

Why Grimoire needs this at all: Tailscale Serve is reachable by everyone on
the tailnet, and this tailnet is shared with friends so they can watch
Jellyfin. Without a login they can read the collection, its dollar value, and
the deck journal. An ACL is the other half of the answer; this half does not
depend on remembering to write one.

Stored in `config/auth.json` (mode 0600, never committed):
  - the username
  - a PBKDF2-SHA256 hash of the password, never the plaintext
  - a per-install signing secret used to sign session cookies

Cookie format:
    <payload_b64url>.<signature_b64url>
where payload is JSON {"u": <username>, "iat": <unix-ts>} and the signature is
HMAC-SHA256(payload, session_secret).

CLI:
    uv run python -m backend.auth set-password <password> [--username NAME]
    uv run python -m backend.auth disable
    uv run python -m backend.auth status
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import pathlib
import secrets
import sys
import time
from typing import Any

CONFIG_PATH = pathlib.Path(
    os.environ.get("GRIMOIRE_AUTH_CONFIG",
                   pathlib.Path(__file__).resolve().parent.parent / "config" / "auth.json"))

# Slow enough to be costly to brute force, fast enough that login feels
# instant on the mini (~50 ms).
_PBKDF2_ITERATIONS = 240_000
_PBKDF2_ALGO = "sha256"
_SALT_BYTES = 16
_HASH_PREFIX = "pbkdf2_sha256"

COOKIE_NAME = "grimoire_session"


# --------------------------------------------------------------------------- #
# Password hashing
# --------------------------------------------------------------------------- #

def hash_password(password: str) -> str:
    """Self-describing hash: 'pbkdf2_sha256$<iters>$<salt_b64>$<hash_b64>'."""
    if not password:
        raise ValueError("password must be non-empty")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"),
                                 salt, _PBKDF2_ITERATIONS)
    return (f"{_HASH_PREFIX}${_PBKDF2_ITERATIONS}"
            f"${base64.b64encode(salt).decode('ascii')}"
            f"${base64.b64encode(digest).decode('ascii')}")


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time comparison. Returns False on malformed input rather than
    raising -- the caller is on the public login path."""
    if not password or not encoded:
        return False
    try:
        algo, iters_str, salt_b64, hash_b64 = encoded.split("$", 3)
        if algo != _HASH_PREFIX:
            return False
        iters = int(iters_str)
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
    except (ValueError, TypeError):
        return False
    candidate = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iters)
    return hmac.compare_digest(candidate, expected)


# --------------------------------------------------------------------------- #
# Session cookies
# --------------------------------------------------------------------------- #

def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode((s + "=" * (-len(s) % 4)).encode("ascii"))


def issue_cookie(username: str, secret: str) -> str:
    payload = json.dumps({"u": username, "iat": int(time.time())},
                         separators=(",", ":")).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64url_encode(payload)}.{_b64url_encode(sig)}"


def verify_cookie(token: str | None, secret: str,
                  max_age_days: int | None = None) -> str | None:
    """Return the authenticated username, or None on any failure."""
    if not token or not secret:
        return None
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _b64url_decode(payload_b64)
        sig = _b64url_decode(sig_b64)
    except (ValueError, TypeError):
        return None
    expected = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        data = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    username = data.get("u")
    if not isinstance(username, str) or not username:
        return None
    if max_age_days:
        issued = data.get("iat")
        if not isinstance(issued, int):
            return None
        if time.time() - issued > max_age_days * 86400:
            return None
    return username


# --------------------------------------------------------------------------- #
# Config access
# --------------------------------------------------------------------------- #

def load_auth_config() -> tuple[dict[str, Any], bool, str | None]:
    """Returns (cfg, enabled, error)."""
    if not CONFIG_PATH.is_file():
        return {}, False, None
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, False, f"cannot read {CONFIG_PATH}: {e}"
    if not isinstance(cfg, dict):
        return {}, False, f"{CONFIG_PATH} is not a JSON object"
    if not cfg.get("enabled", True):
        return cfg, False, None
    missing = [k for k in ("username", "password_hash", "session_secret") if not cfg.get(k)]
    if missing:
        return cfg, False, f"auth config missing {', '.join(missing)}"
    return cfg, True, None


def current_user(token: str | None) -> str | None:
    """One-call check used by the middleware and the auth routes."""
    cfg, enabled, err = load_auth_config()
    if err is not None or not enabled:
        return None
    return verify_cookie(token, str(cfg["session_secret"]),
                         cfg.get("max_age_days"))


def save_auth_config(cfg: dict[str, Any]) -> None:
    """Write 0600 and replace atomically, so a crash mid-write cannot leave a
    half-written file that locks you out."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _cli() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.auth")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("set-password", help="Hash a password and write config/auth.json")
    sp.add_argument("password", help="New password (hashed; never stored plaintext)")
    sp.add_argument("--username", default=None, help="Username (default: keep existing, else 'rocco')")
    sp.add_argument("--days", type=int, default=None,
                    help="Keep the login alive this many days. Omit for a "
                         "session cookie that dies when the browser closes.")

    sub.add_parser("disable", help="Turn auth off (leaves the password in place)")
    sub.add_parser("enable", help="Turn auth back on")
    sub.add_parser("status", help="Show whether auth is configured and enabled")
    sub.add_parser("generate-secret", help="Print a fresh signing secret and exit")
    sub.add_parser("hash", help="Print a hash of a password and exit").add_argument("password")

    args = parser.parse_args()
    existing, _enabled, _err = load_auth_config()

    if args.cmd == "hash":
        print(hash_password(args.password))
        return 0

    if args.cmd == "generate-secret":
        print(secrets.token_hex(32))
        return 0

    if args.cmd == "status":
        cfg, enabled, err = load_auth_config()
        if err:
            print(f"misconfigured: {err}")
            return 1
        if not cfg:
            print(f"not configured — no {CONFIG_PATH}. Anyone on the tailnet can read everything.")
            return 1
        life = f"{cfg['max_age_days']} days" if cfg.get("max_age_days") else "until the browser closes"
        print(f"enabled={enabled} username={cfg.get('username')} session lasts {life}")
        return 0

    if args.cmd in ("disable", "enable"):
        if not existing:
            print(f"nothing to change — no {CONFIG_PATH}")
            return 1
        existing["enabled"] = args.cmd == "enable"
        save_auth_config(existing)
        print(f"auth {'enabled' if existing['enabled'] else 'disabled'}")
        return 0

    if args.cmd == "set-password":
        cfg = {
            "enabled": True,
            "username": args.username or existing.get("username") or "rocco",
            "password_hash": hash_password(args.password),
            # Reused so changing the password does not log out other devices
            # unnecessarily; delete config/auth.json to rotate it.
            "session_secret": existing.get("session_secret") or secrets.token_hex(32),
        }
        days = args.days if args.days is not None else existing.get("max_age_days")
        if days:
            cfg["max_age_days"] = days
        save_auth_config(cfg)
        life = f"{days} days" if days else "until the browser closes"
        print(f"auth updated: username={cfg['username']}, session lasts {life}")
        print(f"written to {CONFIG_PATH}")
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(_cli())
