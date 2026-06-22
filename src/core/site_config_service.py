from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any, Iterable, Optional
from urllib.parse import urlparse

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.config import (
    BASE_SETTINGS_SNAPSHOT,
    Settings,
    coerce_setting_value,
    load_settings_overrides_from_file,
    project_root_path,
    refresh_settings_from_override_file_if_changed,
    reload_settings_from_overrides,
    settings,
    write_settings_overrides_to_file,
)
from core.identity import normalize_host, parse_csv_hosts
from core.logging_config import logger
from core.plan_catalog import load_plan_catalog, normalize_catalog, save_plan_catalog
from core.security_policy import normalize_cookie_samesite, validate_secret_strength
from db.models import SiteConfig, SiteConfigAuditLog, V2User


@dataclass(frozen=True)
class ConfigSpec:
    key: str
    group: str
    group_label: str
    label: str
    description: str
    is_secret: bool
    requires_restart: bool
    storage: str = "settings"
    catalog_path: tuple[str, ...] | None = None


GROUP_LABELS = {
    "speech": "语音识别",
    "security": "认证与安全",
    "portal_email": "官网与邮件",
    "translation": "翻译能力",
    "database": "数据库与存储",
    "update": "更新分发",
    "billing": "计费与爱发电",
    "routing": "版本路由",
    "test_user": "测试账户",
    "runtime": "运行时",
    "tts": "语音合成",
}

GROUP_DESCRIPTIONS = {
    "speech": "识别服务主机、端口、后端与模型配置。",
    "security": "JWT、会话与后台安全相关配置。",
    "portal_email": "官网域名、邮箱验证码与 SMTP 配置。",
    "translation": "翻译服务、Gemma 翻译与能力缓存配置。",
    "database": "数据库连接池、Redis 限流与本地持久化配置。",
    "update": "客户端更新、静态文件与资源清单路径配置。",
    "billing": "爱发电、免费额度、套餐目录与加油包配置。",
    "routing": "v1/v2 域名匹配、版本开关与兼容策略。",
    "test_user": "默认测试账户与测试配额配置。",
    "runtime": "日志、限流、压缩与公共测试基础配置。",
    "tts": "TTS 服务地址与令牌配置。",
}

LABEL_OVERRIDES = {
    "OCR_IMAGE_TRANSLATE_ENABLED": "OCR 图片翻译开关",
    "OCR_IMAGE_TRANSLATE_BASE_URL": "OCR 图片翻译地址",
    "OCR_IMAGE_TRANSLATE_TIMEOUT": "OCR 图片翻译超时",
    "OCR_IMAGE_TRANSLATE_FALLBACK_TO_GEMMA": "OCR 失败回退 Gemma",
    "WHISPER_HOST": "Whisper 主机",
    "WHISPER_PORT": "Whisper 端口",
    "WHISPER_ENDPOINTS": "Whisper 后端列表",
    "WHISPER_BACKEND": "Whisper 后端",
    "WHISPER_TRT_HOST": "Whisper TRT 主机",
    "WHISPER_TRT_PORT": "Whisper TRT 端口",
    "SENSEVOICE_HOST": "SenseVoice 主机",
    "SENSEVOICE_PORT": "SenseVoice 端口",
    "ASR_DEFAULT_ENGINE": "默认 ASR 引擎",
    "FIRERED_ASR_HOST": "FireRedASR2S 主机",
    "FIRERED_ASR_PORT": "FireRedASR2S 端口",
    "FIRERED_ASR_TIMEOUT": "FireRedASR2S 超时",
    "FIRERED_LID_ENABLED": "FireRedLID 启用开关",
    "FIRERED_LID_HOST": "FireRedLID 主机",
    "FIRERED_LID_PORT": "FireRedLID 端口",
    "FIRERED_LID_TIMEOUT": "FireRedLID 超时",
    "FIRERED_LID_MAX_CONCURRENCY": "FireRedLID 最大并发",
    "FIRERED_LID_QUEUE_TIMEOUT": "FireRedLID 排队超时",
    "FIRERED_LID_GLOBAL_MIN_INTERVAL": "FireRedLID 全局冷却秒数",
    "FIRERED_LID_HIGH_CONFIDENCE": "FireRedLID 高置信阈值",
    "FIRERED_LID_LOW_CONFIDENCE": "FireRedLID 低置信阈值",
    "FIRERED_LID_MIN_AUDIO_SECONDS": "FireRedLID 最短音频秒数",
    "WHISPER_APIKEY": "Whisper API Key",
    "JWT_SECRET_KEY": "JWT 密钥",
    "SESSION_SECRET_KEY": "会话密钥",
    "PORTAL_PUBLIC_BASE_URL": "官网公开地址",
    "SQL_PATH": "数据库连接串",
    "LIMITER_REDIS_URL": "Redis 地址",
    "PLAN_CATALOG_PATH": "套餐目录路径",
    "API_V1_HOSTS": "V1 域名白名单",
    "API_V2_HOSTS": "V2 域名白名单",
    "DEFAULT_TEST_USER_UUID": "默认测试用户 UUID",
    "DEFAULT_TEST_USER_EMAIL": "默认测试用户邮箱",
    "DEFAULT_TEST_USER_IP_RATE_LIMIT": "默认测试用户限流",
    "AFDIAN_USER_ID": "爱发电用户 ID",
    "AFDIAN_TOKEN": "爱发电 Token",
    "AFDIAN_CUSTOM_ID_KEY": "爱发电自定义 ID 密钥",
    "AFDIAN_WEBHOOK_FORWARD_URL": "Webhook 转发地址",
    "AFDIAN_WEBHOOK_FORWARD_AUTHORIZATION": "Webhook 转发鉴权",
    "EDGE_TTS_ENABLED": "Edge TTS 启用开关",
    "TTS_URL": "Edge TTS 地址",
    "TTS_TOKEN": "Edge TTS 令牌",
    "EDGE_TTS_TIMEOUT": "Edge TTS 超时",
    "EDGE_TTS_MAX_INPUT_CHARS": "Edge TTS 最大输入字符数",
    "VOICE_SYNTHESIS_LEGACY_TRANSPORT_COMPAT": "语音合成旧客户端传输兼容",
    "QWEN_TTS_ENABLED": "Qwen TTS 启用开关",
    "QWEN_TTS_BUILTIN_VOICES_ENABLED": "Qwen 内置音色启用开关",
    "QWEN_TTS_BASE_URL": "Qwen TTS 地址",
    "QWEN_TTS_TOKEN": "Qwen TTS 令牌",
    "QWEN_TTS_CLONE_MAX_CONCURRENCY": "Qwen 克隆最大上游并发",
    "QWEN_TTS_CLONE_QUEUE_SIZE": "Qwen 克隆等待队列大小",
    "QWEN_TTS_CLONE_QUEUE_TIMEOUT": "Qwen 克隆排队超时",
    "QWEN_TTS_CLONE_RETRY_ATTEMPTS": "Qwen 克隆 429 重试次数",
    "QWEN_TTS_CLONE_RETRY_DELAY": "Qwen 克隆 429 重试退避",
    "QWEN_TTS_CLONE_GLOBAL_LOCK_ENABLED": "Qwen 克隆跨进程全局锁",
    "QWEN_TTS_CLONE_GLOBAL_LOCK_PATH": "Qwen 克隆跨进程锁文件",
    "MOSS_TTS_ENABLED": "MOSS TTS 启用开关",
    "MOSS_TTS_BASE_URL": "MOSS TTS 地址",
    "MOSS_TTS_TOKEN": "MOSS TTS 令牌",
    "OMNIVOICE_TTS_ENABLED": "OmniVoice TTS 启用开关",
    "OMNIVOICE_TTS_BASE_URL": "OmniVoice TTS 地址",
    "OMNIVOICE_TTS_TOKEN": "OmniVoice TTS 令牌",
}

PLAN_LABEL_OVERRIDES = {
    "billing.subscription_mode": "订阅模式 (subscription_mode)",
    "billing.ladder.base_price": "阶梯基础价格",
    "billing.ladder.min_monthly_price": "阶梯最小月费",
    "billing.ladder.max_monthly_price": "阶梯最大月费",
    "billing.ladder.price_step": "阶梯价格步长",
    "billing.ladder.base_daily_quota": "阶梯基础日额度",
    "billing.ladder.daily_quota_step": "阶梯日额度步长",
    "free_plan.daily_quota": "免费用户日额度",
    "free_plan.resource_quota": "免费用户资源额度",
    "free_plan.rate_limit": "免费用户限流规则",
    "default_test_plan.daily_quota": "默认测试用户日额度",
    "default_test_plan.resource_quota": "默认测试用户资源额度",
    "default_test_plan.rate_limit": "默认测试用户限流规则",
    "tiers": "固定档套餐列表",
    "addons": "加油包列表",
}

PLAN_DESCRIPTIONS = {
    "billing.subscription_mode": "资费目录中的订阅模式配置，可选 fixed_tiers 或 ladder。",
    "billing.ladder.base_price": "阶梯模式的基础价格。",
    "billing.ladder.min_monthly_price": "阶梯模式允许的最小月费。",
    "billing.ladder.max_monthly_price": "阶梯模式允许的最大月费。",
    "billing.ladder.price_step": "阶梯模式价格步长。",
    "billing.ladder.base_daily_quota": "阶梯模式基础日额度。",
    "billing.ladder.daily_quota_step": "阶梯模式每档增加的日额度。",
    "free_plan.daily_quota": "免费用户默认日额度。",
    "free_plan.resource_quota": "免费用户默认资源额度。",
    "free_plan.rate_limit": "免费用户默认限流规则。",
    "default_test_plan.daily_quota": "默认测试用户日额度。",
    "default_test_plan.resource_quota": "默认测试用户资源额度。",
    "default_test_plan.rate_limit": "默认测试用户限流规则。",
    "tiers": "固定档套餐完整列表，使用 JSON 数组编辑。",
    "addons": "加油包完整列表，使用 JSON 数组编辑。",
}

PLAN_CATALOG_SPEC_DEFINITIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("billing.subscription_mode", ("billing", "subscription_mode")),
    ("billing.ladder.base_price", ("billing", "ladder", "base_price")),
    ("billing.ladder.min_monthly_price", ("billing", "ladder", "min_monthly_price")),
    ("billing.ladder.max_monthly_price", ("billing", "ladder", "max_monthly_price")),
    ("billing.ladder.price_step", ("billing", "ladder", "price_step")),
    ("billing.ladder.base_daily_quota", ("billing", "ladder", "base_daily_quota")),
    ("billing.ladder.daily_quota_step", ("billing", "ladder", "daily_quota_step")),
    ("free_plan.daily_quota", ("free_plan", "daily_quota")),
    ("free_plan.resource_quota", ("free_plan", "resource_quota")),
    ("free_plan.rate_limit", ("free_plan", "rate_limit")),
    ("default_test_plan.daily_quota", ("default_test_plan", "daily_quota")),
    ("default_test_plan.resource_quota", ("default_test_plan", "resource_quota")),
    ("default_test_plan.rate_limit", ("default_test_plan", "rate_limit")),
    ("tiers", ("tiers",)),
    ("addons", ("addons",)),
)

SECRET_KEYS = {
    "WHISPER_APIKEY",
    "JWT_SECRET_KEY",
    "SESSION_SECRET_KEY",
    "SMTP_PASSWORD",
    "AFDIAN_TOKEN",
    "AFDIAN_CUSTOM_ID_KEY",
    "AFDIAN_WEBHOOK_FORWARD_AUTHORIZATION",
    "TTS_TOKEN",
    "QWEN_TTS_TOKEN",
    "MOSS_TTS_TOKEN",
    "OMNIVOICE_TTS_TOKEN",
    "DEFAULT_TEST_USER_PASSWORD",
}

RESTART_REQUIRED_KEYS = {
    "JWT_SECRET_KEY",
    "SESSION_SECRET_KEY",
    "SESSION_COOKIE_HTTPS_ONLY",
    "SESSION_COOKIE_SAME_SITE",
    "SESSION_COOKIE_MAX_AGE",
    "SECURITY_ENFORCE_STRONG_SECRETS",
    "SQL_PATH",
    "SQL_POOL_SIZE",
    "SQL_MAX_OVERFLOW",
    "SQL_POOL_RECYCLE",
    "UPDATE_STATIC_ROOT",
    "UPDATE_MANIFEST_PATH",
    "MODEL_MANIFEST_PATH",
    "TRANSLATION_PROFILE_MANIFEST_PATH",
    "TRANSLATOR_RUNTIME_MANIFEST_PATH",
    "PLAN_CATALOG_PATH",
    "PORTAL_SESSION_KEY",
    "PORTAL_SESSION_USER_KEY",
    "API_V1_ENABLED",
    "API_V2_ENABLED",
    "API_V2_AUTH_ENABLED",
    "API_V2_PORTAL_ENABLED",
}

STRING_MAX_LENGTH = 4000
CSV_HOST_KEYS = {"API_V1_HOSTS", "API_V2_HOSTS"}
URL_KEYS = {
    "PORTAL_PUBLIC_BASE_URL",
    "FILTER_WEB_URL",
    "PACKAGE_BASE_URL",
    "UPDATE_PUBLIC_BASE_URL",
    "UPDATE_APP_RELEASE_MANIFEST_URL",
    "UPDATE_APP_RELEASE_APPCAST_URL",
    "UPDATE_APP_RELEASE_FALLBACK_URL",
    "AFDIAN_ORDER_CREATE_BASE_URL",
    "AFDIAN_QUERY_ORDER_URL",
    "AFDIAN_WEBHOOK_FORWARD_URL",
    "TTS_URL",
    "OCR_IMAGE_TRANSLATE_BASE_URL",
    "QWEN_TTS_BASE_URL",
    "MOSS_TTS_BASE_URL",
    "OMNIVOICE_TTS_BASE_URL",
}
REDIS_URL_KEYS = {"LIMITER_REDIS_URL"}
EMAIL_KEYS = {"SMTP_USERNAME", "SMTP_FROM_EMAIL", "DEFAULT_TEST_USER_EMAIL"}
HOST_KEYS = {"WHISPER_HOST", "WHISPER_TRT_HOST", "SENSEVOICE_HOST", "FIRERED_ASR_HOST", "FIRERED_LID_HOST", "SMTP_HOST"}
HOST_PORT_KEYS = {"GEMMA_TRANSLATE_URL"}
PATH_KEYS = {
    "UPDATE_STATIC_ROOT",
    "UPDATE_MANIFEST_PATH",
    "MODEL_MANIFEST_PATH",
    "TRANSLATION_PROFILE_MANIFEST_PATH",
    "TRANSLATOR_RUNTIME_MANIFEST_PATH",
    "PLAN_CATALOG_PATH",
    "QWEN_TTS_CLONE_GLOBAL_LOCK_PATH",
}
POSITIVE_INT_KEYS = {
    "WHISPER_PORT",
    "WHISPER_TRT_PORT",
    "SENSEVOICE_PORT",
    "FIRERED_ASR_PORT",
    "FIRERED_LID_PORT",
    "FIRERED_LID_MAX_CONCURRENCY",
    "SMTP_PORT",
    "EMAIL_CODE_LENGTH",
    "EMAIL_CODE_TTL_MINUTES",
    "JWT_ACCESS_TOKEN_EXPIRES_MINUTES",
    "REFRESH_TOKEN_EXPIRES_DAYS",
    "PORTAL_SESSION_EXPIRES_DAYS",
    "SESSION_COOKIE_MAX_AGE",
    "SQL_POOL_SIZE",
    "SQL_MAX_OVERFLOW",
    "SQL_POOL_RECYCLE",
    "TRANSLATION_CAPABILITIES_CACHE_SECONDS",
    "UPDATE_APP_RELEASE_CACHE_SECONDS",
    "AFDIAN_RENEW_DAYS",
    "AFDIAN_BOOST_DAYS",
    "AFDIAN_BOOST_DAILY_INCREMENT",
    "AFDIAN_ADDON_RESOURCE_INCREMENT",
    "AFDIAN_QUERY_SCAN_PAGES",
    "AFDIAN_CUSTOM_ID_TTL_SECONDS",
    "DEFAULT_TEST_USER_IP_DAILY_QUOTA",
    "DEFAULT_TEST_USER_RESOURCE_QUOTA",
    "GUEST_SESSION_EXPIRES_MINUTES",
    "TEXT_COMPRESSION_MIN_REPEAT",
    "GEMMA_TRANSLATE_MAX_TOKENS",
    "EDGE_TTS_MAX_INPUT_CHARS",
    "QWEN_TTS_MAX_INPUT_CHARS",
    "QWEN_TTS_CLONE_MAX_CONCURRENCY",
    "QWEN_TTS_CLONE_QUEUE_SIZE",
    "QWEN_TTS_CLONE_RETRY_ATTEMPTS",
    "MOSS_TTS_MAX_INPUT_CHARS",
    "OMNIVOICE_TTS_MAX_INPUT_CHARS",
}
POSITIVE_FLOAT_KEYS = {
    "TRANSLATION_TIMEOUT",
    "FIRERED_ASR_TIMEOUT",
    "FIRERED_LID_TIMEOUT",
    "FIRERED_LID_QUEUE_TIMEOUT",
    "FIRERED_LID_GLOBAL_MIN_INTERVAL",
    "FIRERED_LID_HIGH_CONFIDENCE",
    "FIRERED_LID_LOW_CONFIDENCE",
    "FIRERED_LID_MIN_AUDIO_SECONDS",
    "GEMMA_TRANSLATE_TIMEOUT",
    "OCR_IMAGE_TRANSLATE_TIMEOUT",
    "EDGE_TTS_TIMEOUT",
    "QWEN_TTS_TIMEOUT",
    "QWEN_TTS_CLONE_QUEUE_TIMEOUT",
    "QWEN_TTS_CLONE_RETRY_DELAY",
    "MOSS_TTS_TIMEOUT",
    "OMNIVOICE_TTS_TIMEOUT",
    "TRANSLATION_CAPABILITY_TIMEOUT",
    "UPDATE_APP_RELEASE_FETCH_TIMEOUT",
    "AFDIAN_CUSTOM_PRICE",
    "AFDIAN_BOOST_PRICE_STEP",
    "AFDIAN_ADDON_PRICE_STEP",
    "AFDIAN_WEBHOOK_FORWARD_TIMEOUT",
}
LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
JWT_ALGORITHMS = {"HS256", "HS384", "HS512"}
ASR_ENGINE_VALUES = {
    "firered",
    "fireredasr",
    "fireredasr2",
    "fireredasr2s",
    "fire-red",
    "fire-red-asr",
    "sensevoice",
    "sense-voice",
    "whisper",
    "faster-whisper",
    "faster_whisper",
    "openai",
    "whisper-trt",
    "whisper_trt",
    "trt",
}
SAFE_TOKEN_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
SAFE_LIMIT_RULE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789=;_./,:- ")
SAFE_SIMPLE_TEXT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def _infer_group(key: str) -> str:
    if key.startswith(("ASR_", "FIRERED_ASR_", "FIRERED_LID_", "WHISPER_", "SENSEVOICE_")):
        return "speech"
    if key.startswith(("JWT_", "SESSION_", "AUTH_LOGIN_", "LEGACY_LOGIN_", "ADMIN_LOGIN_")):
        return "security"
    if key in {"PORTAL_PUBLIC_BASE_URL", "PORTAL_SESSION_KEY", "PORTAL_SESSION_USER_KEY"}:
        return "portal_email"
    if key.startswith(("SMTP_", "EMAIL_")):
        return "portal_email"
    if key.startswith(("TRANSLATOR_", "TRANSLATION_", "GEMMA_", "OCR_IMAGE_TRANSLATE_")) or key in {"FILTER_WEB_URL", "ENABLE_WEB_TRANSLATORS"}:
        return "translation"
    if key.startswith("SQL_") or key == "LIMITER_REDIS_URL":
        return "database"
    if key.startswith(("LATEST_", "PACKAGE_", "UPDATE_")) or key in {
        "MODEL_MANIFEST_PATH",
        "TRANSLATION_PROFILE_MANIFEST_PATH",
        "TRANSLATOR_RUNTIME_MANIFEST_PATH",
    }:
        return "update"
    if key.startswith("AFDIAN_") or key in {"FREE_PLAN_LIMIT_RULE", "PLAN_CATALOG_PATH"}:
        return "billing"
    if key.startswith(("API_", "LEGACY_")):
        return "routing"
    if key.startswith(("DEFAULT_TEST_", "GUEST_")):
        return "test_user"
    if key.startswith(("EDGE_TTS_", "TTS_", "QWEN_TTS_", "MOSS_TTS_", "OMNIVOICE_TTS_", "VOICE_SYNTHESIS_")):
        return "tts"
    return "runtime"


def _build_label(key: str) -> str:
    if key in PLAN_LABEL_OVERRIDES:
        return PLAN_LABEL_OVERRIDES[key]
    if key in LABEL_OVERRIDES:
        return LABEL_OVERRIDES[key]
    return key.replace("_", " ").title()


def _build_description(key: str, group: str) -> str:
    if key in PLAN_DESCRIPTIONS:
        return PLAN_DESCRIPTIONS[key]
    return f"{GROUP_LABELS[group]}中的 {key} 配置项。"


def _build_settings_specs() -> dict[str, ConfigSpec]:
    specs: dict[str, ConfigSpec] = {}
    for key in Settings.model_fields:
        group = _infer_group(key)
        specs[key] = ConfigSpec(
            key=key,
            group=group,
            group_label=GROUP_LABELS[group],
            label=_build_label(key),
            description=_build_description(key, group),
            is_secret=key in SECRET_KEYS,
            requires_restart=key in RESTART_REQUIRED_KEYS,
        )
    return specs


def _build_plan_catalog_specs() -> dict[str, ConfigSpec]:
    specs: dict[str, ConfigSpec] = {}
    for key, catalog_path in PLAN_CATALOG_SPEC_DEFINITIONS:
        specs[key] = ConfigSpec(
            key=key,
            group="billing",
            group_label=GROUP_LABELS["billing"],
            label=_build_label(key),
            description=_build_description(key, "billing"),
            is_secret=False,
            requires_restart=False,
            storage="plan_catalog",
            catalog_path=catalog_path,
        )
    return specs


CONFIG_SPECS = {
    **_build_settings_specs(),
    **_build_plan_catalog_specs(),
}


def _infer_tts_engine(key: str) -> Optional[str]:
    if key.startswith("EDGE_TTS_") or key.startswith("TTS_"):
        return "edge"
    if key.startswith("QWEN_TTS_"):
        return "qwen"
    if key.startswith("MOSS_TTS_"):
        return "moss"
    if key.startswith("OMNIVOICE_TTS_"):
        return "omnivoice"
    return None


def _config_sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
    engine_order = {
        "edge": 0,
        "qwen": 1,
        "moss": 2,
        "omnivoice": 3,
    }
    engine = item.get("engine")
    return (str(item["group"]), engine_order.get(engine, 99), str(item["key"]))


def _validate_group(group: Optional[str]) -> Optional[str]:
    normalized = (group or "").strip()
    if not normalized:
        return None
    if normalized not in GROUP_LABELS:
        raise ValueError(f"不允许的配置分组 {normalized}")
    return normalized


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_json_dumps(value).encode("utf-8")).hexdigest()


def _mask_secret(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if not text:
        return ""
    if len(text) <= 6:
        return "*" * len(text)
    return f"{text[:2]}***{text[-2:]}"


def _summary_for_value(value: Any, is_secret: bool) -> str:
    if is_secret:
        return _mask_secret(value)
    if value is None:
        return "null"
    if isinstance(value, (dict, list)):
        return _json_dumps(value)
    return str(value)


def _value_type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    return type(value).__name__


def _ensure_no_control_chars(value: str, key: str) -> str:
    if any(ord(char) < 32 for char in value if char not in ("\t",)):
        raise ValueError(f"{key} 包含非法控制字符")
    return value


def _validate_common_string(value: str, key: str) -> str:
    normalized = _ensure_no_control_chars(value.strip(), key)
    if len(normalized) > STRING_MAX_LENGTH:
        raise ValueError(f"{key} 长度超出限制")
    return normalized


def _validate_safe_simple_text(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if normalized and any(char not in SAFE_SIMPLE_TEXT_CHARS for char in normalized):
        raise ValueError(f"{key} 包含非法字符")
    return normalized


def _validate_host(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if not normalized:
        raise ValueError(f"{key} 不能为空")
    try:
        ip_address(normalized)
        return normalized
    except ValueError:
        pass
    host = normalize_host(normalized)
    if not host or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789.-" for char in host):
        raise ValueError(f"{key} 不是合法主机名")
    if ".." in host:
        raise ValueError(f"{key} 不是合法主机名")
    return normalized


def _validate_host_port(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if ":" not in normalized:
        raise ValueError(f"{key} 必须是 host:port 格式")
    host, port_text = normalized.rsplit(":", 1)
    _validate_host(host, key)
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"{key} 端口非法") from exc
    if port <= 0 or port > 65535:
        raise ValueError(f"{key} 端口非法")
    return f"{host}:{port}"


def _validate_url(value: str, key: str, *, schemes: Iterable[str]) -> str:
    normalized = _validate_common_string(value, key)
    parsed = urlparse(normalized)
    if parsed.scheme not in set(schemes) or not parsed.netloc:
        raise ValueError(f"{key} 不是合法 URL")
    return normalized


def _validate_csv_hosts(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    hosts = parse_csv_hosts(normalized)
    for host in hosts:
        _validate_host(host, key)
    return ",".join(hosts)


def _validate_email(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if not normalized:
        return normalized
    if "@" not in normalized or normalized.count("@") != 1:
        raise ValueError(f"{key} 不是合法邮箱地址")
    local, domain = normalized.split("@", 1)
    if not local or not domain:
        raise ValueError(f"{key} 不是合法邮箱地址")
    _validate_host(domain, key)
    return normalized


def _validate_path(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if not normalized:
        raise ValueError(f"{key} 不能为空")
    path = Path(normalized)
    resolved = (project_root_path / path).resolve() if not path.is_absolute() else path.resolve()
    project_root_resolved = project_root_path.resolve()
    if resolved != project_root_resolved and project_root_resolved not in resolved.parents:
        raise ValueError(f"{key} 必须位于项目目录内")
    return str(resolved)


def _validate_sql_path(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if normalized.startswith("sqlite:///"):
        sqlite_path = normalized.removeprefix("sqlite:///")
        validated_path = _validate_path(sqlite_path, key)
        return f"sqlite:///{validated_path}"
    parsed = urlparse(normalized)
    if parsed.scheme not in {"mysql", "mysql+pymysql", "sqlite"}:
        raise ValueError(f"{key} 仅支持 sqlite 或 mysql 连接串")
    return normalized


def _validate_limit_rule(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if any(char not in SAFE_LIMIT_RULE_CHARS for char in normalized):
        raise ValueError(f"{key} 包含不允许的字符")
    return normalized


def _validate_services_list(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    items = [part.strip() for part in normalized.split(",") if part.strip()]
    if not items:
        raise ValueError(f"{key} 不能为空")
    for item in items:
        _validate_safe_simple_text(item, key)
    return ",".join(items)


def _validate_whisper_endpoints(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if not normalized:
        return normalized

    endpoints = [part.strip() for part in re.split(r"[;,]+", normalized) if part.strip()]
    if not endpoints:
        return ""

    validated: list[str] = []
    for endpoint in endpoints:
        candidate = endpoint if "://" in endpoint else f"http://{endpoint}"
        parsed = urlparse(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{key} 包含非法 Whisper 后端地址: {endpoint}")
        host = parsed.hostname or ""
        _validate_host(host, key)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError(f"{key} 包含非法端口: {endpoint}") from exc
        if port is not None and (port <= 0 or port > 65535):
            raise ValueError(f"{key} 包含非法端口: {endpoint}")
        validated.append(endpoint.rstrip("/"))

    return ",".join(validated)


def _validate_log_level(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key).upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(f"{key} 必须是 {', '.join(sorted(LOG_LEVELS))}")
    return normalized


def _validate_jwt_algorithm(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key).upper()
    if normalized not in JWT_ALGORITHMS:
        raise ValueError(f"{key} 不在允许列表中")
    return normalized


def _validate_uuid_string(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    try:
        return str(uuid.UUID(normalized))
    except ValueError as exc:
        raise ValueError(f"{key} 不是合法 UUID") from exc


def _validate_token_string(value: str, key: str) -> str:
    normalized = _validate_common_string(value, key)
    if any(char not in SAFE_TOKEN_CHARS for char in normalized):
        raise ValueError(f"{key} 包含非法字符")
    return normalized


def _post_validate_value(spec: ConfigSpec, value: Any) -> Any:
    if isinstance(value, str):
        value = _validate_common_string(value, spec.key)
        if spec.key in {"JWT_SECRET_KEY", "SESSION_SECRET_KEY", "AFDIAN_CUSTOM_ID_KEY"}:
            return validate_secret_strength(value, field_name=spec.key)
        if spec.key in HOST_KEYS:
            return _validate_host(value, spec.key)
        if spec.key in HOST_PORT_KEYS:
            return _validate_host_port(value, spec.key)
        if spec.key in URL_KEYS:
            return _validate_url(value, spec.key, schemes={"http", "https"})
        if spec.key == "WHISPER_ENDPOINTS":
            return _validate_whisper_endpoints(value, spec.key)
        if spec.key in REDIS_URL_KEYS:
            return _validate_url(value, spec.key, schemes={"redis", "rediss"})
        if spec.key in CSV_HOST_KEYS:
            return _validate_csv_hosts(value, spec.key)
        if spec.key in EMAIL_KEYS:
            return _validate_email(value, spec.key)
        if spec.key in PATH_KEYS:
            return _validate_path(value, spec.key)
        if spec.key == "SQL_PATH":
            return _validate_sql_path(value, spec.key)
        if spec.key in {
            "FREE_PLAN_LIMIT_RULE",
            "AFDIAN_DEFAULT_LIMIT_RULE",
            "AFDIAN_BOOST_LIMIT_RULE",
            "DEFAULT_TEST_USER_IP_RATE_LIMIT",
            "AUTH_LOGIN_IP_LIMIT",
            "AUTH_LOGIN_IDENTITY_LIMIT",
            "LEGACY_LOGIN_IP_LIMIT",
            "LEGACY_LOGIN_IDENTITY_LIMIT",
            "ADMIN_LOGIN_IP_LIMIT",
            "ADMIN_LOGIN_IDENTITY_LIMIT",
            "GUEST_LOGIN_IP_LIMIT",
        }:
            return _validate_limit_rule(value, spec.key)
        if spec.key == "SESSION_COOKIE_SAME_SITE":
            return normalize_cookie_samesite(value)
        if spec.key == "TRANSLATOR_SERVICES_LIST":
            return _validate_services_list(value, spec.key)
        if spec.key == "ASR_DEFAULT_ENGINE":
            normalized_engine = _validate_token_string(value, spec.key).lower()
            if normalized_engine not in ASR_ENGINE_VALUES:
                raise ValueError(f"{spec.key} must be one of {', '.join(sorted(ASR_ENGINE_VALUES))}")
            return normalized_engine
        if spec.key == "LOG_LEVEL":
            return _validate_log_level(value, spec.key)
        if spec.key == "JWT_ALGORITHM":
            return _validate_jwt_algorithm(value, spec.key)
        if spec.key == "DEFAULT_TEST_USER_UUID":
            return _validate_uuid_string(value, spec.key)
        if spec.key in {
            "PORTAL_SESSION_KEY",
            "PORTAL_SESSION_USER_KEY",
            "TRANSLATOR_SERVICE",
            "WHISPER_BACKEND",
            "PACKAGE_TYPE",
            "GEMMA_TRANSLATE_API_MODE",
            "GEMMA_TRANSLATE_MODEL",
            "OCR_IMAGE_TRANSLATE_INPAINT_METHOD",
            "OCR_IMAGE_TRANSLATE_RENDER_STYLE",
        }:
            return _validate_token_string(value, spec.key)

    if spec.key in POSITIVE_INT_KEYS and int(value) <= 0:
        raise ValueError(f"{spec.key} 必须大于 0")
    if spec.key in POSITIVE_FLOAT_KEYS and float(value) <= 0:
        raise ValueError(f"{spec.key} 必须大于 0")
    if spec.key in {"FIRERED_LID_HIGH_CONFIDENCE", "FIRERED_LID_LOW_CONFIDENCE"} and not 0 < float(value) <= 1:
        raise ValueError(f"{spec.key} 必须在 0 到 1 之间")
    if spec.key == "FIRERED_LID_HIGH_CONFIDENCE" and float(value) < float(settings.FIRERED_LID_LOW_CONFIDENCE):
        raise ValueError(f"{spec.key} 必须大于等于 FIRERED_LID_LOW_CONFIDENCE")
    if spec.key == "FIRERED_LID_LOW_CONFIDENCE" and float(value) > float(settings.FIRERED_LID_HIGH_CONFIDENCE):
        raise ValueError(f"{spec.key} 必须小于等于 FIRERED_LID_HIGH_CONFIDENCE")
    if spec.key in {"WHISPER_PORT", "WHISPER_TRT_PORT", "SENSEVOICE_PORT", "FIRERED_ASR_PORT", "FIRERED_LID_PORT", "SMTP_PORT"} and int(value) > 65535:
        raise ValueError(f"{spec.key} 端口超出范围")
    return value


def validate_config_value(key: str, value: Any) -> Any:
    spec = CONFIG_SPECS.get(key)
    if spec is None:
        raise ValueError(f"不允许修改配置项 {key}")
    if value is None:
        return None
    if spec.storage == "plan_catalog":
        raise ValueError(f"请使用资费目录校验逻辑处理 {key}")
    parsed = coerce_setting_value(key, value)
    return _post_validate_value(spec, parsed)


def _spec_for_key(key: str) -> ConfigSpec:
    spec = CONFIG_SPECS.get(key)
    if spec is None:
        raise ValueError(f"不允许修改配置项 {key}")
    return spec


def _plan_catalog_base() -> dict[str, Any]:
    return normalize_catalog()


def _plan_catalog_effective() -> dict[str, Any]:
    return load_plan_catalog()


def _catalog_get_value(catalog: dict[str, Any], path: tuple[str, ...] | None) -> Any:
    if not path:
        return catalog
    current: Any = catalog
    for segment in path:
        current = current[segment]
    return current


def _catalog_set_value(catalog: dict[str, Any], path: tuple[str, ...] | None, value: Any) -> None:
    if not path:
        raise ValueError("catalog_path is required")
    current: Any = catalog
    for segment in path[:-1]:
        current = current[segment]
    current[path[-1]] = value


def _clone_json_value(value: Any) -> Any:
    return json.loads(_json_dumps(value))


def _coerce_plan_catalog_value(spec: ConfigSpec, value: Any) -> Any:
    if isinstance(value, str):
        normalized = _validate_common_string(value, spec.key)
        if spec.key in {"tiers", "addons"}:
            try:
                return json.loads(normalized)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{spec.key} 必须是合法 JSON") from exc
        return normalized
    return value


def _validate_plan_catalog_value(spec: ConfigSpec, value: Any, current_catalog: dict[str, Any]) -> Any:
    candidate_catalog = _clone_json_value(current_catalog)
    _catalog_set_value(candidate_catalog, spec.catalog_path, _coerce_plan_catalog_value(spec, value))
    normalized_catalog = normalize_catalog(candidate_catalog)
    return _catalog_get_value(normalized_catalog, spec.catalog_path)


def _base_value(key: str) -> Any:
    spec = _spec_for_key(key)
    if spec.storage == "plan_catalog":
        return _catalog_get_value(_plan_catalog_base(), spec.catalog_path)
    return getattr(BASE_SETTINGS_SNAPSHOT, key)


def _effective_value(key: str) -> Any:
    spec = _spec_for_key(key)
    if spec.storage == "plan_catalog":
        return _catalog_get_value(_plan_catalog_effective(), spec.catalog_path)
    return getattr(settings, key)


def _serialize_item(
    spec: ConfigSpec,
    record: Optional[SiteConfig],
    override_keys: set[str],
    *,
    base_catalog: dict[str, Any] | None = None,
    effective_catalog: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if spec.storage == "plan_catalog":
        if base_catalog is None:
            base_catalog = _plan_catalog_base()
        if effective_catalog is None:
            effective_catalog = _plan_catalog_effective()
        effective_value = _catalog_get_value(effective_catalog, spec.catalog_path)
        base_value = _catalog_get_value(base_catalog, spec.catalog_path)
        is_overridden = _fingerprint(effective_value) != _fingerprint(base_value)
    else:
        effective_value = getattr(settings, spec.key)
        base_value = getattr(BASE_SETTINGS_SNAPSHOT, spec.key)
        is_overridden = spec.key in override_keys
    return {
        "key": spec.key,
        "group": spec.group,
        "group_label": spec.group_label,
        "engine": _infer_tts_engine(spec.key),
        "label": spec.label,
        "description": spec.description,
        "is_secret": spec.is_secret,
        "requires_restart": spec.requires_restart,
        "source": "override" if is_overridden else "base",
        "value": _summary_for_value(effective_value, spec.is_secret),
        "raw_value": None if spec.is_secret else effective_value,
        "base_value": _summary_for_value(base_value, spec.is_secret),
        "base_raw_value": None if spec.is_secret else base_value,
        "updated_at": record.updated_at.isoformat() if record and record.updated_at else None,
        "updated_by_username": record.updated_by_username if record else None,
        "updated_note": record.updated_note if record else None,
    }


def list_site_configs(db: Session, group: Optional[str] = None) -> dict[str, Any]:
    refresh_settings_from_override_file_if_changed()
    group = _validate_group(group)
    records = {item.config_key: item for item in db.query(SiteConfig).all()}
    override_keys = set(load_settings_overrides_from_file().keys())
    base_catalog = _plan_catalog_base()
    effective_catalog = _plan_catalog_effective()
    items = []
    for spec in CONFIG_SPECS.values():
        if group and spec.group != group:
            continue
        items.append(
            _serialize_item(
                spec,
                records.get(spec.key),
                override_keys,
                base_catalog=base_catalog,
                effective_catalog=effective_catalog,
            )
        )
    items.sort(key=_config_sort_key)
    groups = [
        {
            "key": key,
            "label": GROUP_LABELS[key],
            "description": GROUP_DESCRIPTIONS[key],
            "count": sum(1 for item in items if item["group"] == key),
        }
        for key in GROUP_LABELS
        if not group or key == group
    ]
    return {"groups": groups, "items": items}


def list_site_config_audit_logs(
    db: Session,
    *,
    config_key: Optional[str] = None,
    group: Optional[str] = None,
    limit: int = 100,
) -> dict[str, Any]:
    group = _validate_group(group)
    if config_key:
        _spec_for_key(config_key)
    normalized_limit = max(1, min(int(limit or 100), 500))
    query = db.query(SiteConfigAuditLog)
    if config_key:
        query = query.filter(SiteConfigAuditLog.config_key == config_key)
    if group:
        query = query.filter(SiteConfigAuditLog.config_group == group)
    rows = query.order_by(desc(SiteConfigAuditLog.created_at), desc(SiteConfigAuditLog.id)).limit(normalized_limit).all()
    return {
        "items": [
            {
                "id": str(row.id),
                "config_key": row.config_key,
                "config_group": row.config_group,
                "operation": row.operation,
                "is_secret": row.is_secret,
                "requires_restart": row.requires_restart,
                "old_value": row.old_value_summary,
                "new_value": row.new_value_summary,
                "old_fingerprint": row.old_value_fingerprint,
                "new_fingerprint": row.new_value_fingerprint,
                "changed_by_user_id": str(row.changed_by_user_id) if row.changed_by_user_id is not None else None,
                "changed_by_username": row.changed_by_username,
                "changed_from_ip": row.changed_from_ip,
                "change_note": row.change_note,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
    }


def update_site_configs(
    db: Session,
    *,
    actor: V2User,
    request_ip: str,
    updates: list[dict[str, Any]],
    change_note: Optional[str] = None,
) -> dict[str, Any]:
    note = (change_note or "").strip() or None
    if note:
        note = _validate_common_string(note, "change_note")

    original_overrides = dict(load_settings_overrides_from_file())
    next_overrides = dict(original_overrides)
    original_plan_catalog = _plan_catalog_effective()
    next_plan_catalog = _clone_json_value(original_plan_catalog)
    base_plan_catalog = _plan_catalog_base()
    records = {item.config_key: item for item in db.query(SiteConfig).all()}
    changed_count = 0
    settings_changed = False
    plan_catalog_changed = False

    for item in updates:
        key = str(item.get("key") or "").strip()
        clear = bool(item.get("clear", False))
        spec = _spec_for_key(key)
        if spec.storage == "plan_catalog":
            old_effective = _catalog_get_value(next_plan_catalog, spec.catalog_path)
            base_value = _catalog_get_value(base_plan_catalog, spec.catalog_path)
            was_overridden = _fingerprint(old_effective) != _fingerprint(base_value)
        else:
            old_effective = getattr(settings, key)
            base_value = getattr(BASE_SETTINGS_SNAPSHOT, key)
            was_overridden = key in next_overrides
        old_summary = _summary_for_value(old_effective, spec.is_secret)
        old_fingerprint = _fingerprint(old_effective)
        old_json = None if spec.is_secret else _json_dumps(old_effective)
        record = records.get(key)

        if clear:
            new_effective = base_value
            if not was_overridden and old_fingerprint == _fingerprint(new_effective):
                continue
            if spec.storage == "plan_catalog":
                _catalog_set_value(next_plan_catalog, spec.catalog_path, _clone_json_value(base_value))
                plan_catalog_changed = True
            else:
                next_overrides.pop(key, None)
                settings_changed = True
            if record is not None:
                db.delete(record)
                records.pop(key, None)
            operation = "clear"
        else:
            if spec.storage == "plan_catalog":
                new_effective = _validate_plan_catalog_value(spec, item.get("value"), next_plan_catalog)
                if old_fingerprint == _fingerprint(new_effective):
                    continue
                _catalog_set_value(next_plan_catalog, spec.catalog_path, _clone_json_value(new_effective))
                plan_catalog_changed = True
            else:
                new_effective = validate_config_value(key, item.get("value"))
                if key in next_overrides and old_effective == new_effective:
                    continue
                next_overrides[key] = new_effective
                settings_changed = True
            if old_fingerprint == _fingerprint(new_effective):
                continue
            if record is None:
                record = SiteConfig(config_key=key, config_group=spec.group)
                db.add(record)
                records[key] = record
                operation = "create"
            else:
                operation = "update"

            record.config_group = spec.group
            record.value_json = _json_dumps(new_effective)
            record.value_type = _value_type_name(new_effective)
            record.is_secret = spec.is_secret
            record.requires_restart = spec.requires_restart
            record.value_fingerprint = _fingerprint(new_effective)
            record.updated_by_user_id = actor.id
            record.updated_by_username = actor.username
            record.updated_from_ip = request_ip
            record.updated_note = note

        new_summary = _summary_for_value(new_effective, spec.is_secret)
        new_fingerprint = _fingerprint(new_effective)
        new_json = None if spec.is_secret else _json_dumps(new_effective)

        db.add(
            SiteConfigAuditLog(
                config_key=key,
                config_group=spec.group,
                operation=operation,
                is_secret=spec.is_secret,
                requires_restart=spec.requires_restart,
                old_value_json=old_json,
                new_value_json=new_json,
                old_value_summary=old_summary,
                new_value_summary=new_summary,
                old_value_fingerprint=old_fingerprint,
                new_value_fingerprint=new_fingerprint,
                changed_by_user_id=actor.id,
                changed_by_username=actor.username,
                changed_from_ip=request_ip,
                change_note=note,
            )
        )
        changed_count += 1

    if changed_count == 0:
        return {
            "applied": False,
            "message": "没有需要更新的配置项",
            **list_site_configs(db),
        }

    try:
        if settings_changed:
            normalized_overrides = write_settings_overrides_to_file(next_overrides)
            reload_settings_from_overrides(normalized_overrides)
        if plan_catalog_changed:
            next_plan_catalog = save_plan_catalog(next_plan_catalog)
        db.commit()
    except Exception:
        db.rollback()
        try:
            if settings_changed:
                write_settings_overrides_to_file(original_overrides)
                reload_settings_from_overrides(original_overrides)
            if plan_catalog_changed:
                save_plan_catalog(original_plan_catalog)
        except Exception as restore_exc:  # pylint: disable=broad-except
            logger.exception("[ADMIN-CONFIG] failed to restore previous overrides: %s", restore_exc)
        raise

    logger.info(
        "[ADMIN-CONFIG] updated=%s actor=%s ip=%s",
        changed_count,
        actor.username,
        request_ip,
    )
    return {
        "applied": True,
        "message": "配置已保存",
        **list_site_configs(db),
    }
