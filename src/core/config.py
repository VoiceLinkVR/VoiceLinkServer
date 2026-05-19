import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Optional

from pydantic import TypeAdapter, ValidationError
from pydantic_settings import BaseSettings

from core.identity import parse_csv_hosts


current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.dirname(current_dir)
project_root_path = Path(project_root)
SITE_CONFIG_OVERRIDE_PATH = str(project_root_path / "data" / "site_config_overrides.json")
config_logger = logging.getLogger(__name__)
_settings_override_lock = threading.Lock()
_settings_override_signature: tuple[bool, int, int] | None = None


class Settings(BaseSettings):
    WHISPER_HOST: str = "127.0.0.1"
    WHISPER_PORT: int = 8000
    WHISPER_BACKEND: str = "faster-whisper"
    WHISPER_TRT_HOST: str = "127.0.0.1"
    WHISPER_TRT_PORT: int = 8000
    SENSEVOICE_HOST: str = "127.0.0.1"
    SENSEVOICE_PORT: int = 8800
    ASR_DEFAULT_ENGINE: str = "firered"
    FIRERED_ASR_HOST: str = "192.168.2.81"
    FIRERED_ASR_PORT: int = 8008
    FIRERED_ASR_TIMEOUT: float = 120.0
    WHISPER_APIKEY: str = "something"
    JWT_SECRET_KEY: str = "aaargwgwqewe"
    SESSION_SECRET_KEY: str = "feefadfagagagwadsee"
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRES_DAYS: int = 30
    PORTAL_SESSION_EXPIRES_DAYS: int = 30
    PORTAL_SESSION_KEY: str = "portal_session_id"
    PORTAL_SESSION_USER_KEY: str = "portal_user_id"
    SESSION_COOKIE_HTTPS_ONLY: bool = True
    SESSION_COOKIE_SAME_SITE: str = "lax"
    SESSION_COOKIE_MAX_AGE: int = 2592000
    SECURITY_ENFORCE_STRONG_SECRETS: bool = True
    PORTAL_PUBLIC_BASE_URL: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 465
    SMTP_USERNAME: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM_EMAIL: Optional[str] = None
    SMTP_FROM_NAME: str = "VoiceLinkVR"
    SMTP_USE_SSL: bool = True
    EMAIL_CODE_LENGTH: int = 6
    EMAIL_CODE_TTL_MINUTES: int = 10
    JWT_ALGORITHM: str = "HS256"
    WHISPER_MODEL: str = "Systran/faster-whisper-large-v3"
    SQL_PATH: str = f"sqlite:///{project_root}/data/db/users.db"
    SQL_POOL_SIZE: int = 50
    SQL_MAX_OVERFLOW: int = 150
    SQL_POOL_RECYCLE: int = 3600
    FILTER_WEB_URL: Optional[str] = "https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json"
    LIMIT_ENABLE: bool = True
    LEGACY_CLIENT_COMPAT: bool = False
    LIMIT_PUBLIC_TEST_USER: Optional[str] = None
    ENABLE_WEB_TRANSLATORS: bool = False
    TRANSLATOR_SERVICE: str = "alibaba"
    TRANSLATOR_SERVICES_LIST: str = "bing,iciba,alibaba,MyMemory,google"
    TRANSLATION_TIMEOUT: float = 1.5
    GEMMA_TRANSLATE_ENABLED: bool = False
    GEMMA_TRANSLATE_URL: str = "127.0.0.1:8080"
    GEMMA_TRANSLATE_API_MODE: str = "completion"
    GEMMA_TRANSLATE_MODEL: str = "translategemma-12b-it-MP-GGUF"
    GEMMA_TRANSLATE_TIMEOUT: float = 10.0
    GEMMA_TRANSLATE_MAX_TOKENS: int = 500
    GEMMA_TRANSLATE_FALLBACK: bool = True
    OCR_IMAGE_TRANSLATE_ENABLED: bool = False
    OCR_IMAGE_TRANSLATE_BASE_URL: str = "http://127.0.0.1:18000"
    OCR_IMAGE_TRANSLATE_TIMEOUT: float = 180.0
    OCR_IMAGE_TRANSLATE_INPAINT_METHOD: str = "inpaint"
    OCR_IMAGE_TRANSLATE_RENDER_STYLE: str = "localized"
    OCR_IMAGE_TRANSLATE_TEXT_FILL: Optional[str] = None
    OCR_IMAGE_TRANSLATE_INCLUDE_SEGMENTS: bool = True
    OCR_IMAGE_TRANSLATE_FALLBACK_TO_GEMMA: bool = True
    LIMITER_REDIS_URL: Optional[str] = "redis://localhost:6379/0"
    EDGE_TTS_ENABLED: bool = False
    TTS_URL: Optional[str] = None
    TTS_TOKEN: Optional[str] = None
    EDGE_TTS_TIMEOUT: float = 60.0
    EDGE_TTS_MAX_INPUT_CHARS: int = 500
    QWEN_TTS_ENABLED: bool = True
    QWEN_TTS_BUILTIN_VOICES_ENABLED: bool = False
    QWEN_TTS_BASE_URL: str = "http://127.0.0.1:8010"
    QWEN_TTS_TOKEN: Optional[str] = None
    QWEN_TTS_TIMEOUT: float = 300.0
    QWEN_TTS_MAX_INPUT_CHARS: int = 500
    MOSS_TTS_ENABLED: bool = False
    MOSS_TTS_BASE_URL: str = "http://127.0.0.1:8000"
    MOSS_TTS_TOKEN: Optional[str] = None
    MOSS_TTS_TIMEOUT: float = 300.0
    MOSS_TTS_MAX_INPUT_CHARS: int = 500
    OMNIVOICE_TTS_ENABLED: bool = False
    OMNIVOICE_TTS_BASE_URL: str = "http://127.0.0.1:8000"
    OMNIVOICE_TTS_TOKEN: Optional[str] = None
    OMNIVOICE_TTS_TIMEOUT: float = 300.0
    OMNIVOICE_TTS_MAX_INPUT_CHARS: int = 500
    LATEST_VERSION: Optional[str] = None
    PACKAGE_BASE_URL: Optional[str] = None
    PACKAGE_TYPE: Optional[str] = None
    UPDATE_PUBLIC_BASE_URL: Optional[str] = None
    UPDATE_APP_RELEASE_MANIFEST_URL: str = "https://cloudflarestorage.boyqiu001.top/app-releases/upload-manifest.json"
    UPDATE_APP_RELEASE_APPCAST_URL: str = "https://cloudflarestorage.boyqiu001.top/app-releases/appcast.xml"
    UPDATE_APP_RELEASE_FALLBACK_URL: Optional[str] = None
    UPDATE_APP_RELEASE_CACHE_SECONDS: int = 300
    UPDATE_APP_RELEASE_FETCH_TIMEOUT: float = 15.0
    VOICE_CLONE_PRESET_ROOT: str = f"{project_root}/data/voice_clone_presets"
    VOICE_CLONE_PRESET_MAX_UPLOAD_MB: int = 512
    UPDATE_STATIC_ROOT: str = f"{project_root}/data/update/files"
    UPDATE_MANIFEST_PATH: str = f"{project_root}/data/update/update_manifest.json"
    MODEL_MANIFEST_PATH: str = f"{project_root}/data/update/models_manifest.json"
    TRANSLATION_PROFILE_MANIFEST_PATH: str = f"{project_root}/data/update/translation_profile_manifest.json"
    TRANSLATOR_RUNTIME_MANIFEST_PATH: str = f"{project_root}/data/update/translator_runtime_manifest.json"
    SUPPORT_TICKET_ATTACHMENT_ROOT: str = f"{project_root}/data/support_ticket_attachments"
    SUPPORT_TICKET_ATTACHMENT_MAX_FILES: int = 5
    SUPPORT_TICKET_ATTACHMENT_MAX_MB: int = 10
    TRANSLATION_CAPABILITIES_CACHE_SECONDS: int = 1800
    TRANSLATION_CAPABILITY_TIMEOUT: float = 4.0
    LOG_LEVEL: str = "INFO"
    ENABLE_TEXT_COMPRESSION: bool = True
    TEXT_COMPRESSION_MIN_REPEAT: int = 5
    AFDIAN_USER_ID: str = "db43f694299511ef821252540025c377"
    AFDIAN_TOKEN: str = ""
    AFDIAN_ORDER_CREATE_BASE_URL: str = "https://ifdian.net/order/create"
    AFDIAN_QUERY_ORDER_URL: str = "https://afdian.com/api/open/query-order"
    AFDIAN_CUSTOM_ID_KEY: str = "change-me-to-32-byte-secret"
    AFDIAN_CUSTOM_PRICE: float = 5.0
    AFDIAN_RENEW_DAYS: int = 30
    AFDIAN_RENEW_GRACE_DAYS: int = 0
    AFDIAN_BOOST_DAYS: int = 30
    AFDIAN_BOOST_PRICE_STEP: float = 5.0
    AFDIAN_BOOST_DAILY_INCREMENT: int = 3000
    FREE_PLAN_LIMIT_RULE: str = "800/day;8/minute"
    AFDIAN_DEFAULT_LIMIT_RULE: str = "1500/day"
    AFDIAN_BOOST_LIMIT_RULE: str = ""
    AFDIAN_ADDON_PRICE_STEP: float = 5.0
    AFDIAN_ADDON_RESOURCE_INCREMENT: int = 5000
    PLAN_CATALOG_PATH: str = f"{project_root}/data/plan_catalog.json"
    AFDIAN_QUERY_SCAN_PAGES: int = 3
    AFDIAN_CUSTOM_ID_TTL_SECONDS: int = 604800
    AFDIAN_WEBHOOK_FORWARD_ENABLED: bool = False
    AFDIAN_WEBHOOK_FORWARD_URL: Optional[str] = None
    AFDIAN_WEBHOOK_FORWARD_TIMEOUT: float = 10.0
    AFDIAN_WEBHOOK_FORWARD_AUTHORIZATION: Optional[str] = None

    LEGACY_MODE: bool = False
    API_V1_ENABLED: bool = True
    API_V1_LEGACY_LOGIN_ENABLED: bool = True
    API_V2_ENABLED: bool = True
    API_V2_AUTH_ENABLED: bool = True
    API_V2_PORTAL_ENABLED: bool = True
    API_V2_BILLING_ENABLED: bool = False
    API_V2_SELF_ACTIVATE_ENABLED: bool = False
    API_V2_LEGACY_MIGRATION_ENABLED: bool = True
    API_V1_HOSTS: str = ""
    API_V2_HOSTS: str = ""
    AUTH_LOGIN_IP_LIMIT: str = "20/10 minutes"
    AUTH_LOGIN_IDENTITY_LIMIT: str = "8/10 minutes"
    LEGACY_LOGIN_IP_LIMIT: str = "20/10 minutes"
    LEGACY_LOGIN_IDENTITY_LIMIT: str = "8/10 minutes"
    ADMIN_LOGIN_IP_LIMIT: str = "10/10 minutes"
    ADMIN_LOGIN_IDENTITY_LIMIT: str = "5/10 minutes"
    ADMIN_SURFACE_LAN_ONLY: bool = True
    GUEST_AUTH_ENABLED: bool = True
    GUEST_LOGIN_IP_LIMIT: str = "30/10 minutes"
    GUEST_SESSION_EXPIRES_MINUTES: int = 60

    DEFAULT_TEST_USER_ENABLED: bool = True
    DEFAULT_TEST_USER_UUID: str = "00000000-0000-0000-0000-000000000001"
    DEFAULT_TEST_USER_EMAIL: str = "guest@internal.invalid"
    DEFAULT_TEST_USER_ACTIVE: bool = True
    DEFAULT_TEST_USER_IP_DAILY_QUOTA: int = 800
    DEFAULT_TEST_USER_IP_RATE_LIMIT: str = "8/minute"
    DEFAULT_TEST_USER_RESOURCE_QUOTA: int = 0
    DEFAULT_TEST_USER_USERNAME: str = "__guest_internal__"
    DEFAULT_TEST_USER_PASSWORD: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

    @property
    def api_v1_hosts(self) -> list[str]:
        return parse_csv_hosts(self.API_V1_HOSTS)

    @property
    def api_v2_hosts(self) -> list[str]:
        return parse_csv_hosts(self.API_V2_HOSTS)


def _refresh_runtime_urls(active_settings: Settings) -> None:
    global WHISPER_URL, WHISPER_TRT_URL, SENSEVOICE_URL, GEMMA_TRANSLATE_BASE_URL
    WHISPER_URL = f"http://{active_settings.WHISPER_HOST}:{active_settings.WHISPER_PORT}/v1/"
    WHISPER_TRT_URL = f"http://{active_settings.WHISPER_TRT_HOST}:{active_settings.WHISPER_TRT_PORT}"
    SENSEVOICE_URL = f"http://{active_settings.SENSEVOICE_HOST}:{active_settings.SENSEVOICE_PORT}/v1/audio/transcriptions"
    GEMMA_TRANSLATE_BASE_URL = f"http://{active_settings.GEMMA_TRANSLATE_URL}"


def _get_setting_adapter(field_name: str) -> TypeAdapter:
    field = Settings.model_fields[field_name]
    return TypeAdapter(field.annotation)


def coerce_setting_value(field_name: str, value: Any) -> Any:
    try:
        return _get_setting_adapter(field_name).validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"Invalid value for {field_name}: {exc}") from exc


def load_settings_overrides_from_file() -> dict[str, Any]:
    path = Path(SITE_CONFIG_OVERRIDE_PATH)
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # pylint: disable=broad-except
        config_logger.warning("Failed to read settings override file %s: %s", path, exc)
        return {}
    if not isinstance(payload, dict):
        config_logger.warning("Settings override file %s is not a JSON object", path)
        return {}
    return payload


def write_settings_overrides_to_file(overrides: dict[str, Any]) -> dict[str, Any]:
    path = Path(SITE_CONFIG_OVERRIDE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    normalized = {key: overrides[key] for key in sorted(overrides)}
    temp_path = path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(path)
    return normalized


def _read_settings_override_signature(path: Path | None = None) -> tuple[bool, int, int]:
    target = path or Path(SITE_CONFIG_OVERRIDE_PATH)
    try:
        stat = target.stat()
    except FileNotFoundError:
        return (False, 0, 0)
    return (True, stat.st_mtime_ns, stat.st_size)


def apply_settings_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    applied: dict[str, Any] = {}
    for key, raw_value in (overrides or {}).items():
        if key not in Settings.model_fields:
            config_logger.warning("Skipped unknown settings override key: %s", key)
            continue
        try:
            value = coerce_setting_value(key, raw_value)
        except ValueError as exc:
            config_logger.warning("Skipped invalid settings override %s: %s", key, exc)
            continue
        setattr(settings, key, value)
        applied[key] = value
    _refresh_runtime_urls(settings)
    return applied


def reset_settings_to_base() -> None:
    for key in Settings.model_fields:
        setattr(settings, key, getattr(BASE_SETTINGS_SNAPSHOT, key))
    _refresh_runtime_urls(settings)


def reload_settings_from_overrides(overrides: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    reset_settings_to_base()
    return apply_settings_overrides(overrides if overrides is not None else load_settings_overrides_from_file())


def refresh_settings_from_override_file_if_changed() -> bool:
    global _settings_override_signature

    path = Path(SITE_CONFIG_OVERRIDE_PATH)
    try:
        signature = _read_settings_override_signature(path)
    except OSError as exc:
        config_logger.warning("Failed to stat settings override file %s: %s", path, exc)
        return False

    with _settings_override_lock:
        if _settings_override_signature == signature:
            return False
        applied = reload_settings_from_overrides()
        _settings_override_signature = signature
    config_logger.info("Reloaded settings overrides from %s, applied keys=%s", path, len(applied))
    return True


settings = Settings()
BASE_SETTINGS_SNAPSHOT = settings.model_copy(deep=True)
apply_settings_overrides(load_settings_overrides_from_file())
_settings_override_signature = _read_settings_override_signature()

WHISPER_URL = ""
WHISPER_TRT_URL = ""
SENSEVOICE_URL = ""
GEMMA_TRANSLATE_BASE_URL = ""
_refresh_runtime_urls(settings)
