from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import (
    _request_ip,
    _request_session,
    _request_user_agent,
    clear_portal_session,
    create_access_token,
    get_db,
    normalize_email,
    normalize_expiration_utc,
    oauth2_scheme,
    revoke_refresh_tokens_for_session,
    utcnow_naive,
    validate_access_token_payload,
    verify_password,
)
from core.entitlement_service import reset_user_entitlements_to_free
from core.plan_catalog import build_limit_rule, get_free_plan
from db.models import DeviceAuthorization, RefreshToken, UserSession, V2User

REFRESH_TOKEN_IDEMPOTENCY_WINDOW = timedelta(seconds=10)


def fallback_expired_v2_user_to_free(db: Session, user: V2User) -> tuple[bool, Optional[datetime]]:
    expires_at_utc = normalize_expiration_utc(user.expiration_date)
    if expires_at_utc is None or expires_at_utc > datetime.now(timezone.utc):
        return False, expires_at_utc

    reset_user_entitlements_to_free(db, user)
    db.flush()
    return True, expires_at_utc


def authenticate_v2_user(db: Session, login: str, password: str) -> Optional[V2User]:
    normalized_email = normalize_email(login)
    raw_login = (login or "").strip()
    user = (
        db.query(V2User)
        .filter(or_(V2User.email == normalized_email, V2User.username == raw_login))
        .first()
    )
    if user is None or not verify_password(password, user.password):
        return None
    return user


def build_v2_access_token_payload(
    user: V2User,
    *,
    session_id: Optional[str] = None,
    session_type: str = "portal",
    token_type: str = "access",
) -> dict[str, Any]:
    user_uuid = (user.user_uuid or "").strip() or user.email
    return {
        "sub": user_uuid,
        "uid": user.id,
        "sid": session_id,
        "type": token_type,
        "user_uuid": user_uuid,
        "username": user.username,
        "email": (user.email or "").strip(),
        "session_type": session_type,
        "identity_version": int(getattr(user, "identity_version", 2) or 2),
        "account_version": "v2",
    }


def _create_v2_user_session(
    db: Session,
    user: V2User,
    request: Request,
    *,
    session_type: str = "portal",
    expires_delta: Optional[timedelta] = None,
) -> UserSession:
    expires_at = utcnow_naive() + (expires_delta or timedelta(days=settings.PORTAL_SESSION_EXPIRES_DAYS))
    record = UserSession(
        session_id=secrets.token_urlsafe(48),
        user_id=user.id,
        user_uuid=user.user_uuid,
        session_type=session_type,
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
        expires_at=expires_at,
        last_seen_at=utcnow_naive(),
    )
    db.add(record)
    return record


def _bind_v2_portal_session(request: Request, session_record: UserSession) -> None:
    session = _request_session(request)
    if session is None:
        return
    session[settings.PORTAL_SESSION_KEY] = session_record.session_id
    session[settings.PORTAL_SESSION_USER_KEY] = session_record.user_id


def _get_active_v2_portal_session(
    request: Request,
    db: Session,
    *,
    touch: bool = True,
) -> Optional[UserSession]:
    session = _request_session(request)
    if session is None:
        return None

    session_id = session.get(settings.PORTAL_SESSION_KEY)
    if not session_id:
        return None

    record = (
        db.query(UserSession)
        .filter(UserSession.session_id == session_id, UserSession.revoked_at.is_(None))
        .first()
    )
    if record is None:
        clear_portal_session(request)
        return None

    expires_at = normalize_expiration_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        record.revoked_at = utcnow_naive()
        clear_portal_session(request)
        return None

    if touch:
        record.last_seen_at = utcnow_naive()
    return record


def _create_v2_refresh_token(
    db: Session,
    user: V2User,
    *,
    session_id: Optional[str] = None,
) -> tuple[str, RefreshToken]:
    expires_at = utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRES_DAYS)
    token_value = secrets.token_urlsafe(48)
    record = RefreshToken(
        token=token_value,
        user_id=user.id,
        user_uuid=user.user_uuid,
        session_id=session_id,
        expires_at=expires_at,
    )
    db.add(record)
    return token_value, record


def _get_active_v2_user_session_for_update(
    db: Session,
    session_id: str,
    *,
    user_id: Optional[int] = None,
    user_uuid: Optional[str] = None,
) -> Optional[UserSession]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None

    record = (
        db.query(UserSession)
        .filter(
            UserSession.session_id == normalized_session_id,
            UserSession.revoked_at.is_(None),
        )
        .with_for_update()
        .first()
    )
    if record is None:
        return None

    expires_at = normalize_expiration_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        record.revoked_at = utcnow_naive()
        return None

    if user_id is not None and int(record.user_id) != int(user_id):
        return None
    normalized_user_uuid = str(user_uuid or "").strip()
    if normalized_user_uuid and (record.user_uuid or "").strip() not in {"", normalized_user_uuid}:
        return None
    return record


def _revoke_active_v2_refresh_tokens_for_session(db: Session, session_id: str) -> None:
    now = utcnow_naive()
    records = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
        )
        .with_for_update()
        .all()
    )
    for record in records:
        record.revoked_at = now


def _get_active_v2_refresh_token_for_session(db: Session, session_id: str) -> Optional[RefreshToken]:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None

    return (
        db.query(RefreshToken)
        .filter(
            RefreshToken.session_id == normalized_session_id,
            RefreshToken.revoked_at.is_(None),
        )
        .with_for_update()
        .order_by(RefreshToken.id.desc())
        .first()
    )


def issue_v2_login_artifacts(
    db: Session,
    user: V2User,
    request: Request,
    *,
    session_type: str = "portal",
) -> tuple[str, str, UserSession]:
    revoke_v2_user_session(db, request)
    session_record = _create_v2_user_session(db, user, request, session_type=session_type)
    _bind_v2_portal_session(request, session_record)
    access_token = create_access_token(
        build_v2_access_token_payload(
            user,
            session_id=session_record.session_id,
            session_type=session_type,
        )
    )
    refresh_token, _ = _create_v2_refresh_token(db, user, session_id=session_record.session_id)
    return access_token, refresh_token, session_record


def issue_noninteractive_v2_login_artifacts(
    db: Session,
    user: V2User,
    request: Request,
    *,
    session_type: str = "device",
    expires_delta: Optional[timedelta] = None,
    access_token_expires_delta: Optional[timedelta] = None,
) -> tuple[str, str, UserSession]:
    session_record = _create_v2_user_session(db, user, request, session_type=session_type, expires_delta=expires_delta)
    access_token = create_access_token(
        build_v2_access_token_payload(
            user,
            session_id=session_record.session_id,
            session_type=session_type,
        ),
        expires_delta=access_token_expires_delta,
    )
    refresh_token, _ = _create_v2_refresh_token(db, user, session_id=session_record.session_id)
    return access_token, refresh_token, session_record


def revoke_v2_user_session(
    db: Session,
    request: Request,
    *,
    session_id: Optional[str] = None,
) -> Optional[UserSession]:
    session = _request_session(request)
    resolved_session_id = session_id or (session.get(settings.PORTAL_SESSION_KEY) if session is not None else None)
    if not resolved_session_id:
        clear_portal_session(request)
        return None

    record = (
        db.query(UserSession)
        .filter(UserSession.session_id == resolved_session_id, UserSession.revoked_at.is_(None))
        .first()
    )
    if record is not None:
        record.revoked_at = utcnow_naive()
        revoke_refresh_tokens_for_session(db, record.session_id)
    clear_portal_session(request)
    return record


def rotate_v2_refresh_token(db: Session, refresh_token: str) -> tuple[V2User, str, str]:
    candidate_record = db.query(RefreshToken).filter(RefreshToken.token == refresh_token).first()
    if candidate_record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    session_record: UserSession | None = None
    if candidate_record.session_id:
        session_record = _get_active_v2_user_session_for_update(
            db,
            candidate_record.session_id,
            user_id=candidate_record.user_id,
            user_uuid=candidate_record.user_uuid,
        )
        if session_record is None:
            if candidate_record.revoked_at is None:
                candidate_record.revoked_at = utcnow_naive()
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

        db.refresh(candidate_record)
        revoked_at_utc = normalize_expiration_utc(candidate_record.revoked_at)
        if candidate_record.revoked_at is not None and revoked_at_utc is not None:
            now_utc = datetime.now(timezone.utc)
            if now_utc - revoked_at_utc <= REFRESH_TOKEN_IDEMPOTENCY_WINDOW:
                current_refresh = _get_active_v2_refresh_token_for_session(db, candidate_record.session_id)
                if current_refresh is not None:
                    user = None
                    if candidate_record.user_uuid:
                        user = db.query(V2User).filter(V2User.user_uuid == candidate_record.user_uuid).first()
                    if user is None:
                        user = db.query(V2User).filter(V2User.id == candidate_record.user_id).first()
                    if user is None:
                        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
                    if not user.is_active:
                        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

                    session_type = (getattr(session_record, "session_type", None) or "").strip() or "portal"
                    access_token = create_access_token(
                        build_v2_access_token_payload(user, session_id=candidate_record.session_id, session_type=session_type)
                    )
                    return user, access_token, current_refresh.token

    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token == refresh_token, RefreshToken.revoked_at.is_(None))
        .with_for_update()
        .first()
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    expires_at = normalize_expiration_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        record.revoked_at = utcnow_naive()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = None
    if record.user_uuid:
        user = db.query(V2User).filter(V2User.user_uuid == record.user_uuid).first()
    if user is None:
        user = db.query(V2User).filter(V2User.id == record.user_id).first()
    if user is None:
        record.revoked_at = utcnow_naive()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    fallback_expired_v2_user_to_free(db, user)
    if record.session_id:
        _revoke_active_v2_refresh_tokens_for_session(db, record.session_id)
    else:
        record.revoked_at = utcnow_naive()
    new_refresh_token, _ = _create_v2_refresh_token(db, user, session_id=record.session_id)
    session_type = (getattr(session_record, "session_type", None) or "").strip() or "portal"
    new_access_token = create_access_token(
        build_v2_access_token_payload(user, session_id=record.session_id, session_type=session_type)
    )
    return user, new_access_token, new_refresh_token


def _get_v2_user_from_access_token(token: str, db: Session) -> Optional[V2User]:
    payload = validate_access_token_payload(token, db)
    if payload is None:
        return None
    if str(payload.get("account_version") or "").strip().lower() != "v2":
        return None

    user_uuid = str(payload.get("user_uuid") or payload.get("sub") or "").strip()
    user_id = payload.get("uid")
    username = str(payload.get("username") or "").strip()
    email = normalize_email(str(payload.get("email") or "").strip()) if payload.get("email") else ""

    if user_uuid:
        user = db.query(V2User).filter(V2User.user_uuid == user_uuid).first()
        if user is not None:
            return user
    if user_id is not None:
        user = db.query(V2User).filter(V2User.id == user_id).first()
        if user is not None:
            return user
    if email:
        user = db.query(V2User).filter(V2User.email == email).first()
        if user is not None:
            return user
    if username:
        return db.query(V2User).filter(V2User.username == username).first()
    return None


def resolve_authenticated_v2_user(
    request: Request,
    db: Session,
    *,
    token: Optional[str] = None,
    allow_inactive: bool = False,
    allow_expired: bool = False,
) -> Optional[V2User]:
    user = _get_v2_user_from_access_token(token, db) if token else None
    if user is None:
        session_record = _get_active_v2_portal_session(request, db)
        if session_record is not None:
            if session_record.user_uuid:
                user = db.query(V2User).filter(V2User.user_uuid == session_record.user_uuid).first()
            if user is None:
                user = db.query(V2User).filter(V2User.id == session_record.user_id).first()

    if user is None:
        return None
    if not allow_inactive and not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "user_inactive", "message": "User is inactive"})
    if not allow_expired:
        downgraded, _ = fallback_expired_v2_user_to_free(db, user)
        if downgraded:
            db.commit()
    return user


async def get_current_v2_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> V2User:
    user = resolve_authenticated_v2_user(request, db, token=token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_admin_v2_user(current_user: V2User = Depends(get_current_v2_user)) -> V2User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


def get_v2_user_by_device_authorization(db: Session, record: DeviceAuthorization) -> Optional[V2User]:
    if record.user_uuid:
        user = db.query(V2User).filter(V2User.user_uuid == record.user_uuid).first()
        if user is not None:
            return user
    if record.user_id is not None:
        return db.query(V2User).filter(V2User.id == record.user_id).first()
    return None
