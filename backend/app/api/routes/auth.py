from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import Auth, DbSession
from app.core.config import get_settings
from app.core.rate_limit import rate_limiter
from app.core.security import generate_session_token, hash_password, hash_token, needs_rehash, verify_password
from app.models.account import Business, BusinessMembership, User, UserSession
from app.models.enums import MembershipRole
from app.schemas.auth import LoginRequest, LoginResponse, MeResponse, RegisterRequest, UserOut
from app.schemas.business import BusinessOut, validate_timezone
from app.services.audit_service import record_audit
from app.services.notification_service import get_notification_settings

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Equalises timing between "unknown email" and "wrong password".
_DUMMY_HASH = hash_password("timing-equaliser-password-1")


def _client_key(request: Request, email: str) -> str:
    host = request.client.host if request.client else "unknown"
    return f"{host}:{email.lower()}"


def _check_rate(request: Request, email: str) -> None:
    settings = get_settings()
    if not rate_limiter.hit(
        _client_key(request, email), settings.auth_rate_limit_attempts, settings.auth_rate_limit_window_seconds
    ):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts. Please wait a few minutes and try again."
        )


def _start_session(db, request: Request, response: Response, user: User) -> str:
    settings = get_settings()
    token = generate_session_token()
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.session_ttl_hours),
            user_agent=(request.headers.get("user-agent") or "")[:300],
        )
    )
    response.set_cookie(
        settings.session_cookie_name,
        token,
        max_age=settings.session_ttl_hours * 3600,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )
    return token


def _me(db, user: User) -> tuple[Business, str]:
    membership = db.scalar(
        select(BusinessMembership).where(BusinessMembership.user_id == user.id).order_by(BusinessMembership.created_at)
    )
    business = db.get(Business, membership.business_id)
    return business, membership.role


@router.post("/register", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, request: Request, response: Response, db: DbSession) -> LoginResponse:
    """Create a user together with their business workspace and sign them in."""
    _check_rate(request, payload.email)
    try:
        timezone_name = validate_timezone(payload.timezone)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    email = payload.email.lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists")
    user = User(email=email, password_hash=hash_password(payload.password), full_name=payload.full_name)
    business = Business(name=payload.business_name, timezone=timezone_name, currency="INR", date_format="DMY")
    db.add_all([user, business])
    db.flush()
    db.add(BusinessMembership(user_id=user.id, business_id=business.id, role=MembershipRole.OWNER))
    settings = get_notification_settings(db, business.id)
    settings.recipient_emails = [email]  # sensible default; editable in Settings
    record_audit(
        db,
        business_id=business.id,
        user_id=user.id,
        action="business.created",
        entity_type="business",
        entity_id=business.id,
    )
    token = _start_session(db, request, response, user)
    db.commit()
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email, full_name=user.full_name),
        business=BusinessOut.model_validate(business),
        role=MembershipRole.OWNER,
        access_token=token,
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession) -> LoginResponse:
    _check_rate(request, payload.email)
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if not verify_password(payload.password, user.password_hash) or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password")
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)
    token = _start_session(db, request, response, user)
    business, role = _me(db, user)
    db.commit()
    return LoginResponse(
        user=UserOut(id=user.id, email=user.email, full_name=user.full_name),
        business=BusinessOut.model_validate(business),
        role=role,
        access_token=token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(ctx: Auth, response: Response, db: DbSession) -> Response:
    session = db.get(UserSession, ctx.session.id)
    if session is not None:
        session.revoked_at = datetime.now(timezone.utc)
        db.commit()
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    response.delete_cookie(get_settings().session_cookie_name, path="/")
    return response


@router.get("/me", response_model=MeResponse)
def me(ctx: Auth) -> MeResponse:
    return MeResponse(
        user=UserOut(id=ctx.user.id, email=ctx.user.email, full_name=ctx.user.full_name),
        business=BusinessOut.model_validate(ctx.business),
        role=ctx.role,
    )
