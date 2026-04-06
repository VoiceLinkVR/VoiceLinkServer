import smtplib
import ssl
from email.message import EmailMessage

from fastapi import HTTPException, status

from core.config import settings
from core.logging_config import logger


def is_email_delivery_configured() -> bool:
    return all(
        [
            (settings.SMTP_HOST or "").strip(),
            (settings.SMTP_USERNAME or "").strip(),
            (settings.SMTP_PASSWORD or "").strip(),
            (settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME or "").strip(),
        ]
    )


def ensure_email_delivery_configured() -> None:
    if is_email_delivery_configured():
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Email service is not configured",
    )


def send_email_message(*, recipient: str, subject: str, text_body: str) -> None:
    ensure_email_delivery_configured()

    sender_email = (settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME or "").strip()
    sender_name = (settings.SMTP_FROM_NAME or "VoiceLinkVR").strip() or "VoiceLinkVR"

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{sender_name} <{sender_email}>"
    message["To"] = recipient
    message.set_content(text_body)

    host = (settings.SMTP_HOST or "").strip()
    username = (settings.SMTP_USERNAME or "").strip()
    password = settings.SMTP_PASSWORD or ""
    port = int(settings.SMTP_PORT)

    try:
        if settings.SMTP_USE_SSL:
            with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=15) as client:
                client.login(username, password)
                client.send_message(message)
        else:
            with smtplib.SMTP(host, port, timeout=15) as client:
                client.starttls(context=ssl.create_default_context())
                client.login(username, password)
                client.send_message(message)
    except Exception as exc:
        logger.error("Failed to send email to %s: %s", recipient, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Failed to deliver verification email",
        ) from exc
