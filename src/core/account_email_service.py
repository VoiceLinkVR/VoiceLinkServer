import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from core.config import settings
from core.dependencies import normalize_email, utcnow_naive
from core.email_service import send_email_message
from db.models import AccountToken

REGISTER_EMAIL_PURPOSE = "register_verify"
EMAIL_BIND_PURPOSE = "email_verify"
PASSWORD_RESET_PURPOSE = "password_reset"
PASSWORD_CHANGE_PURPOSE = "password_change"


def _code_ttl() -> timedelta:
    return timedelta(minutes=max(int(settings.EMAIL_CODE_TTL_MINUTES), 1))


def build_email_code() -> str:
    length = max(int(settings.EMAIL_CODE_LENGTH), 4)
    digits = "0123456789"
    return "".join(secrets.choice(digits) for _ in range(length))


def invalidate_account_tokens(
    db: Session,
    *,
    username: Optional[str] = None,
    email: Optional[str] = None,
    purpose: Optional[str] = None,
) -> None:
    query = db.query(AccountToken).filter(AccountToken.used_at.is_(None))
    if username is not None:
        query = query.filter(AccountToken.username == username)
    if email is not None:
        query = query.filter(AccountToken.email == normalize_email(email))
    if purpose is not None:
        query = query.filter(AccountToken.purpose == purpose)
    now = utcnow_naive()
    for record in query.all():
        record.used_at = now


def create_email_challenge(
    db: Session,
    *,
    username: str,
    email: str,
    purpose: str,
) -> AccountToken:
    normalized_email = normalize_email(email)
    invalidate_account_tokens(db, username=username, purpose=purpose)
    invalidate_account_tokens(db, email=normalized_email, purpose=purpose)

    record = AccountToken(
        token=secrets.token_urlsafe(32),
        username=username,
        email=normalized_email,
        purpose=purpose,
        code=build_email_code(),
        expires_at=utcnow_naive() + _code_ttl(),
    )
    db.add(record)
    db.flush()
    return record


def get_active_email_challenge(
    db: Session,
    *,
    token: str,
    purpose: str,
    code: Optional[str] = None,
) -> AccountToken:
    record = db.query(AccountToken).filter(AccountToken.token == token, AccountToken.purpose == purpose).first()
    if record is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification token")
    if record.used_at is not None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification token already used")

    expires_at = _to_utc(record.expires_at)
    if expires_at is None or expires_at <= datetime.now(timezone.utc):
        record.used_at = utcnow_naive()
        db.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code expired")

    normalized_code = (code or "").strip()
    if record.code and normalized_code != record.code:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Verification code is incorrect")

    return record


def consume_email_challenge(db: Session, record: AccountToken) -> None:
    record.used_at = utcnow_naive()


def send_email_code(
    *,
    email: str,
    purpose: str,
    code: str,
) -> None:
    ttl_minutes = max(int(settings.EMAIL_CODE_TTL_MINUTES), 1)
    action_label = {
        REGISTER_EMAIL_PURPOSE: "注册",
        EMAIL_BIND_PURPOSE: "绑定邮箱",
        PASSWORD_RESET_PURPOSE: "找回密码",
        PASSWORD_CHANGE_PURPOSE: "修改密码",
    }.get(purpose, "账户验证")

    subject = f"VoiceLinkVR {action_label}验证码"
    body = (
        f"你正在进行 VoiceLinkVR {action_label}操作。\n\n"
        f"验证码：{code}\n"
        f"有效期：{ttl_minutes} 分钟\n\n"
        "如果这不是你的操作，请忽略这封邮件。"
    )
    send_email_message(recipient=normalize_email(email), subject=subject, text_body=body)


def _to_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
