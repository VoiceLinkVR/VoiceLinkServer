import re
import secrets
from datetime import timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.account_email_service import (
    REGISTER_EMAIL_PURPOSE,
    consume_email_challenge,
    create_email_challenge,
    get_active_email_challenge,
    send_email_code,
)
from core.config import settings
from core.dependencies import (
    get_db,
    hash_password,
    issue_noninteractive_login_artifacts,
    normalize_email,
    revoke_refresh_token,
    utcnow_naive,
)
from core.entitlement_service import rebuild_user_entitlement_state_from_legacy_fields
from core.identity import generate_user_uuid, is_v2_identity_ready
from core.plan_catalog import build_limit_rule, get_free_plan
from core.rate_limiter import enforce_auth_attempt_rate_limit
from core.v2_dependencies import (
    authenticate_v2_user,
    fallback_expired_v2_user_to_free,
    get_current_v2_user,
    get_v2_user_by_device_authorization,
    issue_noninteractive_v2_login_artifacts,
    issue_v2_login_artifacts,
    revoke_v2_user_session,
    rotate_v2_refresh_token,
)
from db.models import DeviceAuthorization, User, V2User
from schemas.auth import (
    AuthDeviceActionResponse,
    AuthDeviceAuthorizeRequest,
    AuthDeviceRequestResponse,
    AuthDeviceStartRequest,
    AuthDeviceStartResponse,
    AuthDeviceTokenRequest,
    AuthDeviceTokenResponse,
    AuthLoginRequest,
    AuthLogoutRequest,
    AuthRefreshRequest,
    AuthRegisterCodeRequest,
    AuthRegisterRequest,
    AuthTokenResponse,
    AuthUserResponse,
    AuthVerificationChallengeResponse,
)

router = APIRouter()
DEVICE_AUTH_TTL = timedelta(minutes=10)
DEVICE_AUTH_INTERVAL_SECONDS = 5
DEVICE_AUTH_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _migration_required(user: V2User) -> bool:
    return not is_v2_identity_ready(
        email=getattr(user, "email", None),
        identity_version=getattr(user, "identity_version", 2),
    )


def _build_username(db: Session, email: str) -> str:
    local_part = email.split("@", 1)[0]
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "_", local_part).strip("._-") or "user"
    slug = slug[:32]
    candidate = slug
    counter = 1

    while db.query(V2User).filter(V2User.username == candidate).first() is not None:
        suffix = f"_{counter}"
        candidate = f"{slug[: max(1, 50 - len(suffix))]}{suffix}"
        counter += 1

    return candidate


def _serialize_user(user: V2User) -> AuthUserResponse:
    identity_version = int(getattr(user, "identity_version", 2) or 2)
    migration_required = _migration_required(user)
    return AuthUserResponse(
        id=str(user.id),
        user_uuid=(user.user_uuid or "").strip(),
        username=user.username,
        legacy_username="",
        email=(user.email or "").strip(),
        email_verified=bool(getattr(user, "email_verified", False)),
        is_admin=bool(user.is_admin),
        identity_version=identity_version,
        migration_required=migration_required,
    )


def _serialize_guest_user(user: User) -> AuthUserResponse:
    return AuthUserResponse(
        id=str(user.id),
        user_uuid=(user.user_uuid or "").strip(),
        username="guest",
        legacy_username="",
        email="",
        email_verified=False,
        is_admin=False,
        identity_version=1,
        migration_required=False,
    )


def _get_guest_trial_user(db: Session) -> User | None:
    return (
        db.query(User)
        .filter(User.is_default_test_user == True)  # noqa: E712
        .order_by(User.id.asc())
        .first()
    )


def _normalize_user_code(value: str) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", "", (value or "").upper())
    if len(normalized) == 8:
        return f"{normalized[:4]}-{normalized[4:]}"
    return normalized


def _build_portal_base_url(request: Request) -> str:
    configured_base = (settings.PORTAL_PUBLIC_BASE_URL or "").strip().rstrip("/")
    if configured_base:
        return configured_base
    return f"{str(request.base_url).rstrip('/')}/app"


def _build_device_verification_urls(request: Request, user_code: str) -> tuple[str, str]:
    verification_uri = f"{_build_portal_base_url(request)}/authorize"
    verification_uri_complete = f"{verification_uri}?{urlencode({'user_code': user_code})}"
    return verification_uri, verification_uri_complete


def _generate_device_code() -> str:
    return secrets.token_urlsafe(32)


def _generate_user_code() -> str:
    raw = "".join(secrets.choice(DEVICE_AUTH_CODE_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def _is_device_authorization_expired(record: DeviceAuthorization) -> bool:
    return record.expires_at <= utcnow_naive()


def _mark_device_authorization_expired(record: DeviceAuthorization) -> None:
    if record.status == "pending":
        record.status = "expired"


def _find_device_authorization_by_user_code(db: Session, user_code: str) -> DeviceAuthorization | None:
    normalized = _normalize_user_code(user_code)
    return db.query(DeviceAuthorization).filter(DeviceAuthorization.user_code == normalized).first()


def _serialize_device_request(record: DeviceAuthorization, user: V2User | None = None) -> AuthDeviceRequestResponse:
    return AuthDeviceRequestResponse(
        user_code=record.user_code,
        client_name=(record.client_name or "VRCLS Desktop").strip() or "VRCLS Desktop",
        requested_scope=(record.requested_scope or "vrcls.client").strip() or "vrcls.client",
        status=record.status,
        expires_at=record.expires_at.isoformat() + "Z",
        username=user.username if user is not None else "",
        user_uuid=(user.user_uuid or "").strip() if user is not None else "",
    )


@router.post("/register/send-code", response_model=AuthVerificationChallengeResponse)
async def send_register_code(
    payload: AuthRegisterCodeRequest,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    existing_user = db.query(V2User).filter(or_(V2User.email == email, V2User.username == email)).first()
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    challenge = create_email_challenge(
        db,
        username=email,
        email=email,
        purpose=REGISTER_EMAIL_PURPOSE,
    )
    send_email_code(email=email, purpose=REGISTER_EMAIL_PURPOSE, code=challenge.code or "")
    db.commit()
    return AuthVerificationChallengeResponse(
        message="Verification code sent",
        verification_token=challenge.token,
        expires_in=settings.EMAIL_CODE_TTL_MINUTES * 60,
    )


@router.post("/register", response_model=AuthTokenResponse)
async def register(
    payload: AuthRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    email = normalize_email(payload.email)
    existing_user = db.query(V2User).filter(or_(V2User.email == email, V2User.username == email)).first()
    if existing_user is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email already registered")

    register_challenge = get_active_email_challenge(
        db,
        token=payload.verification_token.strip(),
        purpose=REGISTER_EMAIL_PURPOSE,
        code=payload.verification_code.strip(),
    )
    if normalize_email(register_challenge.email or "") != email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification email does not match")

    free_plan = get_free_plan()
    user_uuid = generate_user_uuid()
    user = V2User(
        user_uuid=user_uuid,
        legacy_user_id=None,
        username=_build_username(db, email),
        password=hash_password(payload.password),
        email=email,
        email_verified=True,
        is_admin=False,
        is_active=True,
        identity_version=2,
        limit_rule=build_limit_rule(free_plan.get("rate_limit"), free_plan["id"], free_plan["daily_quota"]),
        created_at=utcnow_naive(),
        last_login_at=utcnow_naive(),
    )
    db.add(user)
    db.flush()
    rebuild_user_entitlement_state_from_legacy_fields(db, user)
    consume_email_challenge(db, register_challenge)

    access_token, refresh_token, _ = issue_v2_login_artifacts(db, user, request)
    db.commit()
    db.refresh(user)

    return AuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_serialize_user(user),
    )


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    payload: AuthLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    login_value = (payload.email or payload.login or "").strip()
    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="auth_login",
        ip_limit=settings.AUTH_LOGIN_IP_LIMIT,
        identity=login_value,
        identity_limit=settings.AUTH_LOGIN_IDENTITY_LIMIT,
    )
    user = authenticate_v2_user(db, login_value, payload.password)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    fallback_expired_v2_user_to_free(db, user)

    user.last_login_at = utcnow_naive()
    access_token, refresh_token, _ = issue_v2_login_artifacts(db, user, request)
    db.commit()
    db.refresh(user)

    return AuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_serialize_user(user),
    )


@router.post("/guest", response_model=AuthTokenResponse)
async def guest_login(
    request: Request,
    db: Session = Depends(get_db),
):
    if not settings.GUEST_AUTH_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")

    enforce_auth_attempt_rate_limit(
        request,
        scope_prefix="guest_login",
        ip_limit=settings.GUEST_LOGIN_IP_LIMIT,
    )
    user = _get_guest_trial_user(db)
    if user is None or not bool(getattr(user, "is_active", False)):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Guest trial is unavailable")

    access_token, refresh_token, _ = issue_noninteractive_login_artifacts(
        db,
        user,
        request,
        session_type="guest",
        expires_delta=timedelta(minutes=settings.GUEST_SESSION_EXPIRES_MINUTES),
        access_token_expires_delta=timedelta(minutes=settings.GUEST_SESSION_EXPIRES_MINUTES),
    )
    revoke_refresh_token(db, refresh_token)
    user.last_login_at = utcnow_naive()
    db.commit()
    db.refresh(user)

    return AuthTokenResponse(
        access_token=access_token,
        refresh_token="",
        user=_serialize_guest_user(user),
    )


@router.post("/refresh", response_model=AuthTokenResponse)
async def refresh(payload: AuthRefreshRequest, db: Session = Depends(get_db)):
    user, access_token, refresh_token = rotate_v2_refresh_token(db, payload.refresh_token.strip())

    user.last_login_at = utcnow_naive()
    db.commit()
    db.refresh(user)

    return AuthTokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=_serialize_user(user),
    )


@router.post("/logout")
async def logout(
    request: Request,
    payload: AuthLogoutRequest | None = None,
    db: Session = Depends(get_db),
):
    if payload and payload.refresh_token:
        revoke_refresh_token(db, payload.refresh_token.strip())
    revoke_v2_user_session(db, request)
    db.commit()
    return {"message": "Logged out"}


@router.get("/me", response_model=AuthUserResponse)
async def me(current_user: V2User = Depends(get_current_v2_user)):
    return _serialize_user(current_user)


@router.post("/device/start", response_model=AuthDeviceStartResponse)
async def start_device_authorization(
    payload: AuthDeviceStartRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    client_name = (payload.client_name or "VRCLS Desktop").strip() or "VRCLS Desktop"
    requested_scope = (payload.scope or "vrcls.client").strip() or "vrcls.client"

    created_record: DeviceAuthorization | None = None
    for _ in range(5):
        candidate = DeviceAuthorization(
            device_code=_generate_device_code(),
            user_code=_normalize_user_code(_generate_user_code()),
            client_name=client_name,
            requested_scope=requested_scope,
            status="pending",
            ip=request.client.host if request.client is not None else None,
            user_agent=request.headers.get("User-Agent"),
            expires_at=utcnow_naive() + DEVICE_AUTH_TTL,
        )
        db.add(candidate)
        try:
            db.commit()
            db.refresh(candidate)
            created_record = candidate
            break
        except Exception:
            db.rollback()

    if created_record is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to create device authorization")

    verification_uri, verification_uri_complete = _build_device_verification_urls(request, created_record.user_code)
    return AuthDeviceStartResponse(
        device_code=created_record.device_code,
        user_code=created_record.user_code,
        verification_uri=verification_uri,
        verification_uri_complete=verification_uri_complete,
        expires_in=int(DEVICE_AUTH_TTL.total_seconds()),
        interval=DEVICE_AUTH_INTERVAL_SECONDS,
    )


@router.get("/device/requests/{user_code}", response_model=AuthDeviceRequestResponse)
async def get_device_authorization_request(
    user_code: str,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    record = _find_device_authorization_by_user_code(db, user_code)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device authorization request not found")

    if _is_device_authorization_expired(record):
        _mark_device_authorization_expired(record)
        db.commit()

    linked_user = None
    if record.user_uuid:
        linked_user = db.query(V2User).filter(V2User.user_uuid == record.user_uuid).first()
    if linked_user is None and record.user_id:
        linked_user = db.query(V2User).filter(V2User.id == record.user_id).first()

    if linked_user is not None and linked_user.id != current_user.id and record.status in {"approved", "completed"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This device request has already been authorized")

    return _serialize_device_request(record, linked_user)


@router.post("/device/authorize", response_model=AuthDeviceActionResponse)
async def authorize_device_request(
    payload: AuthDeviceAuthorizeRequest,
    db: Session = Depends(get_db),
    current_user: V2User = Depends(get_current_v2_user),
):
    record = _find_device_authorization_by_user_code(db, payload.user_code)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device authorization request not found")

    if _is_device_authorization_expired(record):
        _mark_device_authorization_expired(record)
        db.commit()
        return AuthDeviceActionResponse(message="Device authorization request expired", status="expired")

    if record.status == "completed":
        return AuthDeviceActionResponse(message="Device authorization request already completed", status="completed")
    if record.status == "denied":
        return AuthDeviceActionResponse(message="Device authorization request already denied", status="denied")
    if record.status == "approved":
        if record.user_uuid and record.user_uuid != current_user.user_uuid:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="This device request has already been authorized")
        return AuthDeviceActionResponse(message="Device authorization request already approved", status="approved")

    decision = (payload.decision or "approve").strip().lower()
    if decision not in {"approve", "deny"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported device authorization decision")

    record.user_id = current_user.id
    record.user_uuid = current_user.user_uuid
    if decision == "deny":
        record.status = "denied"
        record.denied_at = utcnow_naive()
        db.commit()
        return AuthDeviceActionResponse(message="Device authorization request denied", status="denied")

    record.status = "approved"
    record.approved_at = utcnow_naive()
    db.commit()
    return AuthDeviceActionResponse(message="Device authorization request approved", status="approved")


@router.post("/device/token", response_model=AuthDeviceTokenResponse)
async def exchange_device_authorization_token(
    payload: AuthDeviceTokenRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    record = (
        db.query(DeviceAuthorization)
        .filter(DeviceAuthorization.device_code == payload.device_code.strip())
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device authorization request not found")

    record.last_polled_at = utcnow_naive()

    if _is_device_authorization_expired(record):
        _mark_device_authorization_expired(record)
        db.commit()
        return AuthDeviceTokenResponse(status="expired", poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)

    if record.status == "pending":
        db.commit()
        return AuthDeviceTokenResponse(status="pending", poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)
    if record.status == "denied":
        db.commit()
        return AuthDeviceTokenResponse(status="denied", poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)
    if record.status == "completed":
        db.commit()
        return AuthDeviceTokenResponse(status="completed", poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)
    if record.status != "approved":
        db.commit()
        return AuthDeviceTokenResponse(status=record.status, poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)

    user = get_v2_user_by_device_authorization(db, record)
    if user is None:
        record.status = "denied"
        db.commit()
        return AuthDeviceTokenResponse(status="denied", poll_interval=DEVICE_AUTH_INTERVAL_SECONDS)

    access_token, refresh_token, _ = issue_noninteractive_v2_login_artifacts(
        db,
        user,
        request,
        session_type="device",
    )
    user.last_login_at = utcnow_naive()
    record.status = "completed"
    record.completed_at = utcnow_naive()
    db.commit()
    db.refresh(user)

    return AuthDeviceTokenResponse(
        status="approved",
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES * 60,
        poll_interval=DEVICE_AUTH_INTERVAL_SECONDS,
        user=_serialize_user(user),
    )
