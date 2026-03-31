from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.orm import Session
from werkzeug.security import check_password_hash, generate_password_hash

from core.config import settings
from db.base import SessionLocal
from db.models import User


def verify_password(password: str, hashed_password: str) -> bool:
    """Verify a password using Werkzeug."""
    return check_password_hash(hashed_password, password)


def hash_password(password: str) -> str:
    """Hash a password using Werkzeug."""
    return generate_password_hash(password)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login", auto_error=False)


def get_db() -> Generator:
    try:
        db = SessionLocal()
        yield db
    finally:
        db.close()


def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=settings.JWT_ACCESS_TOKEN_EXPIRES_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


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
    default_rule = (settings.AFDIAN_DEFAULT_LIMIT_RULE or "").strip() or "800/day;8/minute"
    now_utc = datetime.now(timezone.utc)
    boost_expiration = normalize_expiration_utc(getattr(user, "boost_expiration_date", None))
    boost_rule = (getattr(user, "boost_limit_rule", None) or "").strip()
    if boost_expiration and boost_expiration > now_utc and boost_rule:
        return boost_rule
    return (user.limit_rule or "").strip() or default_rule


async def get_current_user(token: Optional[str] = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "user_inactive", "message": "User is inactive"},
        )

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


async def get_current_user_for_status(
    token: Optional[str] = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_admin_user(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


def get_admin_user_from_session(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user_id = request.session.get("user_id")
    if user_id is None:
        return None
    user = db.query(User).filter(User.id == user_id, User.is_admin == True).first()
    return user
