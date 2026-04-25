from __future__ import annotations

import math
import re


MIN_PASSWORD_LENGTH = 10
MIN_SECRET_LENGTH = 32
ALLOWED_SESSION_COOKIE_SAMESITE = {"lax", "strict", "none"}
INSECURE_DEFAULT_SECRETS = {
    "aaargwgwqewe",
    "feefadfagagagwadsee",
    "change-me-to-32-byte-secret",
}
COMMON_PASSWORDS = {
    "123456",
    "12345678",
    "123456789",
    "1234567890",
    "111111",
    "000000",
    "qwerty",
    "password",
    "password123",
    "admin123",
    "abc123",
    "abc123!",
    "letmein",
}


def validate_password_strength(password: str, *, context: str = "password") -> str:
    value = str(password or "")
    if len(value) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"{context} must be at least {MIN_PASSWORD_LENGTH} characters")
    if any(char.isspace() for char in value):
        raise ValueError(f"{context} must not contain spaces")

    has_lower = any(char.islower() for char in value)
    has_upper = any(char.isupper() for char in value)
    has_digit = any(char.isdigit() for char in value)
    if not (has_lower and has_upper and has_digit):
        raise ValueError(f"{context} must include lowercase, uppercase, and number")

    lowered = value.lower()
    if lowered in COMMON_PASSWORDS:
        raise ValueError(f"{context} is too common")
    if _looks_like_repeated_or_sequential(value):
        raise ValueError(f"{context} is too predictable")
    return value


def validate_secret_strength(secret: str, *, field_name: str, min_length: int = MIN_SECRET_LENGTH) -> str:
    value = str(secret or "").strip()
    if len(value) < min_length:
        raise ValueError(f"{field_name} must be at least {min_length} characters")
    if value in INSECURE_DEFAULT_SECRETS:
        raise ValueError(f"{field_name} is using an insecure default value")
    if _looks_like_repeated_or_sequential(value):
        raise ValueError(f"{field_name} is too predictable")
    if _shannon_entropy_bits(value) < max(64.0, min_length * 2.2):
        raise ValueError(f"{field_name} must be more random")
    return value


def normalize_cookie_samesite(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in ALLOWED_SESSION_COOKIE_SAMESITE:
        raise ValueError(
            f"SESSION_COOKIE_SAME_SITE must be one of: {', '.join(sorted(ALLOWED_SESSION_COOKIE_SAMESITE))}"
        )
    return normalized


def _looks_like_repeated_or_sequential(value: str) -> bool:
    lowered = value.lower()
    if len(set(lowered)) <= 2 and len(lowered) >= 6:
        return True

    if re.fullmatch(r"(.)\1{5,}", lowered):
        return True

    ascending = "abcdefghijklmnopqrstuvwxyz0123456789"
    descending = ascending[::-1]
    for window_size in range(6, min(len(lowered), 12) + 1):
        for start in range(0, len(ascending) - window_size + 1):
            chunk = ascending[start : start + window_size]
            if chunk in lowered or chunk in descending:
                return True
    return False


def _shannon_entropy_bits(value: str) -> float:
    if not value:
        return 0.0
    length = len(value)
    frequencies = [value.count(char) / length for char in set(value)]
    return -sum(freq * math.log2(freq) for freq in frequencies) * length
