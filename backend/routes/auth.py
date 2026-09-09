"""Authentication endpoints.

Public:     GET  /api/auth/status   — does this install require login?
            POST /api/auth/login    — exchange credentials for a session cookie
Protected:  POST /api/auth/logout   — clear the cookie
            GET  /api/auth/me       — who am I?
"""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel

from .. import auth

router = APIRouter(tags=["auth"], prefix="/auth")

# Deliberately crude rate limiting: a single-user app on a private tailnet does
# not need a real bucket, but an unthrottled login endpoint turns a weak
# password into a matter of minutes. Keyed by client host, in memory, reset on
# restart.
_FAILURES: dict[str, list[float]] = {}
_WINDOW_SECONDS = 300
_MAX_FAILURES = 10


def _too_many_failures(host: str) -> bool:
    now = time.time()
    recent = [t for t in _FAILURES.get(host, []) if now - t < _WINDOW_SECONDS]
    _FAILURES[host] = recent
    return len(recent) >= _MAX_FAILURES


def _record_failure(host: str) -> None:
    _FAILURES.setdefault(host, []).append(time.time())


class AuthStatus(BaseModel):
    enabled: bool
    authenticated: bool
    username: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


@router.get("/status", response_model=AuthStatus)
def auth_status(request: Request) -> AuthStatus:
    _cfg, enabled, _err = auth.load_auth_config()
    if not enabled:
        return AuthStatus(enabled=False, authenticated=True, username=None)
    me = auth.current_user(request.cookies.get(auth.COOKIE_NAME))
    return AuthStatus(enabled=True, authenticated=me is not None, username=me)


@router.post("/login")
def login(body: LoginRequest, request: Request, response: Response) -> dict[str, str]:
    cfg, enabled, err = auth.load_auth_config()
    if err is not None:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail=err)
    if not enabled:
        return {"status": "auth disabled"}

    host = request.client.host if request.client else "unknown"
    if _too_many_failures(host):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="too many failed attempts; wait a few minutes")

    ok = (body.username == str(cfg.get("username") or "")
          and auth.verify_password(body.password, str(cfg.get("password_hash") or "")))
    if not ok:
        _record_failure(host)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    _FAILURES.pop(host, None)
    token = auth.issue_cookie(str(cfg["username"]), str(cfg["session_secret"]))

    # Tailscale Serve terminates TLS and forwards over plain HTTP to uvicorn,
    # so request.url.scheme is 'http' even though the browser is on HTTPS.
    # Trust the forwarded header for the Secure flag, and fall back to False so
    # http://127.0.0.1:8787 still works for local development.
    forwarded = request.headers.get("x-forwarded-proto", "")
    secure = forwarded.split(",")[0].strip() == "https"

    response.set_cookie(
        key=auth.COOKIE_NAME,
        value=token,
        httponly=True,          # JS cannot read it, so XSS cannot exfiltrate it
        samesite="lax",
        secure=secure,
        path="/",
        # No Max-Age unless configured: the cookie dies with the browser,
        # matching the dashboard. `set-password --days N` opts into longer.
        max_age=(cfg["max_age_days"] * 86400) if cfg.get("max_age_days") else None,
    )
    return {"status": "ok"}


@router.post("/logout")
def logout(response: Response) -> dict[str, str]:
    response.delete_cookie(auth.COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get("/me")
def me(request: Request) -> dict[str, str | None]:
    return {"username": auth.current_user(request.cookies.get(auth.COOKIE_NAME))}
