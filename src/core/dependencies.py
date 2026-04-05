import secrets
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy import or_
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from core.config import settings
from db.base import SessionLocal
from db.models import RefreshToken, User, UserSession


def verify_password(password: str, hashed_password: str) -> bool:
    return check_password_hash(hashed_password, password)


def hash_password(password: str) -> str:
    return generate_password_hash(password)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


def utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def normalize_expiration_utc(expiration_date: Optional[datetime]) -> Optional[datetime]:
    if expiration_date is None:
        return None
    if expiration_date.tzinfo is None:
        return expiration_date.replace(tzinfo=timezone.utc)
    return expiration_date.astimezone(timezone.utc)


def is_user_expired(user: User) -> tuple[bool, Optional[datetime]]:
    expires_at_utc = normalize_expiration_utc(user.expiration_date)
    if expires_at_utc is None:
        return False, None
    return expires_at_utc <= datetime.now(timezone.utc), expires_at_utc


def get_effective_user_limit_rule(user: User) -> str:
    default_rule = (settings.FREE_PLAN_LIMIT_RULE or "").strip() or "800/day;8/minute"
    now_utc = datetime.now(timezone.utc)
    boost_expiration = normalize_expiration_utc(getattr(user, "boost_expiration_date", None))
    boost_rule = (getattr(user, "boost_limit_rule", None) or "").strip()
    if boost_expiration and boost_expiration > now_utc and boost_rule:
        return boost_rule
    return (user.limit_rule or "").strip() or default_rule


def normalize_email(value: str) -> str:
    return value.strip().lower()


def authenticate_user(db: Session, login: str, password: str) -> Optional[User]:
    normalized_login = normalize_email(login)
    user = db.query(User).filter(or_(User.username == login, User.email == normalized_login)).first()
    if user is None or not verify_password(password, user.password):
        return None
    return user


def _request_ip(request: Request) -> Optional[str]:
    x_real_ip = request.headers.get("X-Real-Ip")
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_real_ip:
        return x_real_ip.split(",")[0].strip()
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    if request.client is not None:
        return request.client.host
    return None


def _request_user_agent(request: Request) -> Optional[str]:
    return request.headers.get("User-Agent")


def _request_session(request: Request) -> Optional[dict]:
    if "session" not in request.scope:
        return None
    return request.session


def create_user_session(
    db: Session,
    user: User,
    request: Request,
    *,
    session_type: str = "portal",
) -> UserSession:
    expires_at = utcnow_naive() + timedelta(days=settings.PORTAL_SESSION_EXPIRES_DAYS)
    record = UserSession(
        session_id=secrets.token_urlsafe(48),
        user_id=user.id,
        session_type=session_type,
        ip=_request_ip(request),
        user_agent=_request_user_agent(request),
        expires_at=expires_at,
        last_seen_at=utcnow_naive(),
    )
    db.add(record)
    return record


def bind_portal_session(request: Request, session_record: UserSession) -> None:
    session = _request_session(request)
    if session is None:
        return
    session[settings.PORTAL_SESSION_KEY] = session_record.session_id
    session[settings.PORTAL_SESSION_USER_KEY] = session_record.user_id


def clear_portal_session(request: Request) -> None:
    session = _request_session(request)
    if session is None:
        return
    session.pop(settings.PORTAL_SESSION_KEY, None)
    session.pop(settings.PORTAL_SESSION_USER_KEY, None)


def get_active_portal_session(
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
        .filter(
            UserSession.session_id == session_id,
            UserSession.session_type == "portal",
            UserSession.revoked_at.is_(None),
        )
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


def create_refresh_token(
    db: Session,
    user: User,
    *,
    session_id: Optional[str] = None,
) -> tuple[str, RefreshToken]:
    expires_at = utcnow_naive() + timedelta(days=settings.REFRESH_TOKEN_EXPIRES_DAYS)
    token_value = secrets.token_urlsafe(48)
    record = RefreshToken(
        token=token_value,
        user_id=user.id,
        session_id=session_id,
        expires_at=expires_at,
    )
    db.add(record)
    return token_value, record


def get_active_refresh_token(db: Session, refresh_token: str) -> Optional[RefreshToken]:
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token == refresh_token, RefreshToken.revoked_at.is_(None))
        .first()
    )
    if record is None:
        return None

    expires_at = normalize_expiration_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        record.revoked_at = utcnow_naive()
        return None
    return record


def revoke_refresh_token(db: Session, refresh_token: str) -> Optional[RefreshToken]:
    record = (
        db.query(RefreshToken)
        .filter(RefreshToken.token == refresh_token, RefreshToken.revoked_at.is_(None))
        .first()
    )
    if record is not None:
        record.revoked_at = utcnow_naive()
    return record


def revoke_refresh_tokens_for_session(db: Session, session_id: str) -> None:
    now = utcnow_naive()
    records = (
        db.query(RefreshToken)
        .filter(RefreshToken.session_id == session_id, RefreshToken.revoked_at.is_(None))
        .all()
    )
    for record in records:
        record.revoked_at = now


def revoke_user_session(
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


def issue_login_artifacts(db: Session, user: User, request: Request) -> tuple[str, str, UserSession]:
    revoke_user_session(db, request)
    session_record = create_user_session(db, user, request)
    bind_portal_session(request, session_record)
    access_token = create_access_token(
        {"sub": user.username, "uid": user.id, "sid": session_record.session_id, "type": "access"}
    )
    refresh_token, _ = create_refresh_token(db, user, session_id=session_record.session_id)
    return access_token, refresh_token, session_record


def issue_noninteractive_login_artifacts(
    db: Session,
    user: User,
    request: Request,
    *,
    session_type: str = "device",
) -> tuple[str, str, UserSession]:
    session_record = create_user_session(db, user, request, session_type=session_type)
    access_token = create_access_token(
        {"sub": user.username, "uid": user.id, "sid": session_record.session_id, "type": "access"}
    )
    refresh_token, _ = create_refresh_token(db, user, session_id=session_record.session_id)
    return access_token, refresh_token, session_record


def rotate_refresh_token(
    db: Session,
    refresh_token: str,
) -> tuple[User, str, str]:
    record = get_active_refresh_token(db, refresh_token)
    if record is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    user = db.query(User).filter(User.id == record.user_id).first()
    if user is None:
        record.revoked_at = utcnow_naive()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token")

    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is inactive")

    expired, expires_at_utc = is_user_expired(user)
    if expired:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "account_expired",
                "message": "User account expired",
                "expiration_utc": expires_at_utc.isoformat().replace("+00:00", "Z") if expires_at_utc else None,
            },
        )

    record.revoked_at = utcnow_naive()
    new_refresh_token, _ = create_refresh_token(db, user, session_id=record.session_id)
    new_access_token = create_access_token(
        {"sub": user.username, "uid": user.id, "sid": record.session_id, "type": "access"}
    )
    return user, new_access_token, new_refresh_token


def _get_user_from_access_token(token: str, db: Session) -> Optional[User]:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        return None

    user_id = payload.get("uid")
    username = payload.get("sub")

    query = db.query(User)
    if user_id is not None:
        user = query.filter(User.id == user_id).first()
        if user is not None:
            return user
    if username is not None:
        return query.filter(User.username == username).first()
    return None


def resolve_authenticated_user(
    request: Request,
    db: Session,
    *,
    token: Optional[str] = None,
    allow_inactive: bool = False,
    allow_expired: bool = False,
) -> Optional[User]:
    user = _get_user_from_access_token(token, db) if token else None
    if user is None:
        session_record = get_active_portal_session(request, db)
        if session_record is not None:
            user = db.query(User).filter(User.id == session_record.user_id).first()

    if user is None:
        return None

    if not allow_inactive and not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "user_inactive", "message": "User is inactive"},
        )

    if not allow_expired:
        expired, expires_at_utc = is_user_expired(user)
        if expired:
            try:
                user.is_active = False
                db.commit()
            except Exception:
                db.rollback()

            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "code": "account_expired",
                    "message": "User account expired",
                    "expiration_utc": expires_at_utc.isoformat().replace("+00:00", "Z") if expires_at_utc else None,
                },
            )

    return user


async def get_current_user(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = resolve_authenticated_user(request, db, token=token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_user_for_status(
    request: Request,
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    user = resolve_authenticated_user(request, db, token=token, allow_inactive=True, allow_expired=True)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


def get_admin_user_from_session(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    session = _request_session(request)
    if session is None:
        return None

    user_id = session.get("user_id")
    if user_id is None:
        return None
    return db.query(User).filter(User.id == user_id, User.is_admin == True).first()
