import os
from pydantic_settings import BaseSettings
from typing import Optional

# 获取当前文件的目录
current_dir = os.path.dirname(os.path.abspath(__file__))
# 获取项目根目录
project_root = os.path.dirname(current_dir)

class Settings(BaseSettings):
    WHISPER_HOST: str = "127.0.0.1"
    WHISPER_PORT: int = 8000
    WHISPER_BACKEND: str = "faster-whisper"
    WHISPER_TRT_HOST: str = "127.0.0.1"
    WHISPER_TRT_PORT: int = 8000
    SENSEVOICE_HOST: str = "127.0.0.1"
    SENSEVOICE_PORT: int = 8800
    WHISPER_APIKEY: str = "something"
    JWT_SECRET_KEY: str = "wVLAF_13N6XL_QmP.DjkKsV"
    SESSION_SECRET_KEY: str = "wVddLAF_13dsdddN6XL_QmP.DjkKsV"
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 10080  # 7 days
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
    TRANSLATOR_SERVICES_LIST: str = "bing,iciba,alibaba,MyMemory,google"  # 翻译供应商列表，按优先级排序
    TRANSLATION_TIMEOUT: float = 1.5  # 翻译请求超时时间（秒）
    GEMMA_TRANSLATE_ENABLED: bool = False
    GEMMA_TRANSLATE_URL: str = "127.0.0.1:8080"
    GEMMA_TRANSLATE_API_MODE: str = "openai"  # new llama-server deployment supports OpenAI-compatible API only
    GEMMA_TRANSLATE_MODEL: str = "translategemma-12b-it-MP-GGUF"
    GEMMA_TRANSLATE_TIMEOUT: float = 10.0
    GEMMA_TRANSLATE_MAX_TOKENS: int = 500
    GEMMA_TRANSLATE_FALLBACK: bool = True
    LIMITER_REDIS_URL: Optional[str] = "redis://localhost:6379/0"
    TTS_URL: Optional[str] = None
    TTS_TOKEN: Optional[str] = None
    LATEST_VERSION: Optional[str] = None
    PACKAGE_BASE_URL: Optional[str] = None
    PACKAGE_TYPE: Optional[str] = None
    UPDATE_PUBLIC_BASE_URL: Optional[str] = None
    UPDATE_STATIC_ROOT: str = f"{project_root}/data/update/files"
    UPDATE_MANIFEST_PATH: str = f"{project_root}/data/update/update_manifest.json"
    MODEL_MANIFEST_PATH: str = f"{project_root}/data/update/models_manifest.json"
    TRANSLATION_PROFILE_MANIFEST_PATH: str = f"{project_root}/data/update/translation_profile_manifest.json"
    TRANSLATOR_RUNTIME_MANIFEST_PATH: str = f"{project_root}/data/update/translator_runtime_manifest.json"
    TRANSLATION_CAPABILITIES_CACHE_SECONDS: int = 1800
    TRANSLATION_CAPABILITY_TIMEOUT: float = 4.0
    LOG_LEVEL: str = "INFO"  # 日志级别，支持 DEBUG, INFO, WARNING, ERROR, CRITICAL
    ENABLE_TEXT_COMPRESSION: bool = True  # 是否启用文本重复字符压缩
    TEXT_COMPRESSION_MIN_REPEAT: int = 5  # 文本压缩最小重复次数（默认5次）
    AFDIAN_USER_ID: str = "db43f694299511ef821252540025c377"
    AFDIAN_TOKEN: str = ""
    AFDIAN_ORDER_CREATE_BASE_URL: str = "https://afdian.com/order/create"
    AFDIAN_QUERY_ORDER_URL: str = "https://afdian.com/api/open/query-order"
    AFDIAN_CUSTOM_ID_KEY: str = "change-me-to-32-byte-secret"
    AFDIAN_CUSTOM_PRICE: float = 5.0
    AFDIAN_RENEW_DAYS: int = 30
    AFDIAN_BOOST_DAYS: int = 30
    AFDIAN_BOOST_PRICE_STEP: float = 5.0
    AFDIAN_BOOST_DAILY_INCREMENT: int = 1500
    AFDIAN_DEFAULT_LIMIT_RULE: str = "1500/day"
    AFDIAN_BOOST_LIMIT_RULE: str = ""
    AFDIAN_QUERY_SCAN_PAGES: int = 3
    AFDIAN_CUSTOM_ID_TTL_SECONDS: int = 604800
    

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()

# Construct URLs from settings
WHISPER_URL = f"http://{settings.WHISPER_HOST}:{settings.WHISPER_PORT}/v1/"
WHISPER_TRT_URL = f"http://{settings.WHISPER_TRT_HOST}:{settings.WHISPER_TRT_PORT}"
SENSEVOICE_URL = f"http://{settings.SENSEVOICE_HOST}:{settings.SENSEVOICE_PORT}/v1/audio/transcriptions"
GEMMA_TRANSLATE_BASE_URL = f"http://{settings.GEMMA_TRANSLATE_URL}"
