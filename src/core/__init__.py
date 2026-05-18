from importlib import import_module
from typing import Any

from .config import SENSEVOICE_URL, WHISPER_TRT_URL, WHISPER_URL, settings

_LAZY_EXPORTS = {
    "admin_service": ("core.admin_service", None),
    "create_access_token": ("core.dependencies", "create_access_token"),
    "enforce_user_rate_limit": ("core.rate_limiter", "enforce_user_rate_limit"),
    "get_admin_user_from_session": ("core.dependencies", "get_admin_user_from_session"),
    "get_client_ip": ("core.rate_limiter", "get_client_ip"),
    "get_current_admin_user": ("core.dependencies", "get_current_admin_user"),
    "get_current_user": ("core.dependencies", "get_current_user"),
    "get_db": ("core.dependencies", "get_db"),
    "hash_password": ("core.dependencies", "hash_password"),
    "logger": ("core.logging_config", "logger"),
    "oauth2_scheme": ("core.dependencies", "oauth2_scheme"),
    "rate_limiter": ("core.rate_limiter", "rate_limiter"),
    "verify_password": ("core.dependencies", "verify_password"),
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _LAZY_EXPORTS[name]
    module = import_module(module_name)
    value = module if attr_name is None else getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = [
    "admin_service",
    "settings",
    "WHISPER_URL",
    "WHISPER_TRT_URL",
    "SENSEVOICE_URL",
    "get_db",
    "get_current_user",
    "get_current_admin_user",
    "create_access_token",
    "verify_password",
    "hash_password",
    "get_admin_user_from_session",
    "oauth2_scheme",
    "logger",
    "rate_limiter",
    "enforce_user_rate_limit",
    "get_client_ip",
]
