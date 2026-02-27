from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache

from fastapi import Header, HTTPException, status
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token

from .config import settings


@dataclass(frozen=True)
class AuthUser:
    email: str
    subject: str | None = None
    name: str | None = None
    picture: str | None = None


def normalize_email(email: str) -> str:
    return email.strip().lower()


def owner_key_from_email(email: str) -> str:
    normalized = normalize_email(email)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return digest[:24]


@lru_cache(maxsize=1)
def _google_request() -> GoogleRequest:
    return GoogleRequest()


def _raise_unauthorized(detail: str) -> None:
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def require_user(authorization: str | None = Header(default=None)) -> AuthUser:
    if settings.webapp_disable_auth:
        email = normalize_email(settings.webapp_dev_user_email)
        return AuthUser(email=email, subject="dev", name=email, picture=None)

    client_id = settings.webapp_google_client_id.strip()
    if not client_id:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="WEBAPP_GOOGLE_CLIENT_ID is not configured",
        )

    if not authorization:
        _raise_unauthorized("Missing Authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.strip().lower() != "bearer" or not token.strip():
        _raise_unauthorized("Invalid Authorization header")

    try:
        claims = id_token.verify_oauth2_token(token.strip(), _google_request(), client_id)
    except Exception as exc:  # noqa: BLE001
        _raise_unauthorized(f"Invalid Google token: {exc}")

    issuer = str(claims.get("iss") or "")
    if issuer not in {"accounts.google.com", "https://accounts.google.com"}:
        _raise_unauthorized("Invalid token issuer")

    if not bool(claims.get("email_verified")):
        _raise_unauthorized("Google email is not verified")

    email = normalize_email(str(claims.get("email") or ""))
    if not email:
        _raise_unauthorized("Google token missing email")

    return AuthUser(
        email=email,
        subject=str(claims.get("sub") or "") or None,
        name=str(claims.get("name") or "") or None,
        picture=str(claims.get("picture") or "") or None,
    )
