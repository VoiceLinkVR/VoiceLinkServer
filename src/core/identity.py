from __future__ import annotations

import uuid
from typing import Iterable


USER_UUID_NAMESPACE = uuid.UUID("8f221f90-f02a-4af0-8bd0-d86f87358840")


def generate_user_uuid() -> str:
    return str(uuid.uuid4())


def generate_stable_user_uuid(user_id: int | str, username: str | None = None) -> str:
    seed = f"user:{user_id}:{(username or '').strip().lower()}"
    return str(uuid.uuid5(USER_UUID_NAMESPACE, seed))


def has_bound_email(email: str | None) -> bool:
    return bool((email or "").strip())


def is_v2_identity_ready(*, email: str | None, identity_version: int | None) -> bool:
    return has_bound_email(email) or int(identity_version or 1) >= 2


def build_default_display_name(*, user_uuid: str | None = None, user_id: int | str | None = None) -> str:
    if user_uuid:
        return f"用户 {short_user_uuid(user_uuid)}"
    if user_id is not None:
        return f"用户 {user_id}"
    return "VoiceLinkVR User"


def short_user_uuid(user_uuid: str | None) -> str:
    normalized = (user_uuid or "").strip()
    if not normalized:
        return ""
    return normalized.split("-", 1)[0]


def parse_csv_hosts(value: str | None) -> list[str]:
    if not value:
        return []
    hosts: list[str] = []
    for item in value.split(","):
        normalized = item.strip().lower()
        if normalized:
            hosts.append(normalized)
    return hosts


def normalize_host(host: str | None) -> str:
    normalized = (host or "").strip().lower()
    if ":" in normalized:
        normalized = normalized.split(":", 1)[0]
    return normalized


def host_matches(host: str | None, configured_hosts: Iterable[str]) -> bool:
    normalized_host = normalize_host(host)
    if not normalized_host:
        return False
    configured = {normalize_host(item) for item in configured_hosts if normalize_host(item)}
    return normalized_host in configured
