"""Request dependencies: database session and authenticated tenant context.

Every protected endpoint receives an `AuthContext`; all queries must filter by
`ctx.business.id`, which is derived from the server-side session, never from
client input.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_token
from app.db.session import get_db
from app.models.account import Business, BusinessMembership, User, UserSession

DbSession = Annotated[Session, Depends(get_db)]

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
CSRF_HEADER = "x-requested-with"


@dataclass
class AuthContext:
    user: User
    business: Business
    role: str
    session: UserSession

    @property
    def business_id(self) -> uuid.UUID:
        return self.business.id

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id


def _extract_token(request: Request) -> tuple[str | None, bool]:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip(), False
    return request.cookies.get(get_settings().session_cookie_name), True


def get_auth(request: Request, db: DbSession) -> AuthContext:
    token, from_cookie = _extract_token(request)
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Not authenticated", headers={"WWW-Authenticate": "Bearer"}
    )
    if not token:
        raise unauthorized
    # Cookie-authenticated state changes need a custom header. Browsers cannot
    # add it cross-site without a CORS preflight, which blocks CSRF.
    if from_cookie and request.method not in SAFE_METHODS and not request.headers.get(CSRF_HEADER):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing X-Requested-With header")
    session = db.scalar(select(UserSession).where(UserSession.token_hash == hash_token(token)))
    now = datetime.now(timezone.utc)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise unauthorized
    user = db.get(User, session.user_id)
    if user is None or not user.is_active:
        raise unauthorized
    membership = db.scalar(
        select(BusinessMembership).where(BusinessMembership.user_id == user.id).order_by(BusinessMembership.created_at)
    )
    if membership is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "No business workspace for this user")
    business = db.get(Business, membership.business_id)
    assert business is not None
    return AuthContext(user=user, business=business, role=membership.role, session=session)


Auth = Annotated[AuthContext, Depends(get_auth)]


@dataclass
class Pagination:
    page: int
    page_size: int

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def get_pagination(
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Pagination:
    return Pagination(page, page_size)


Paging = Annotated[Pagination, Depends(get_pagination)]
