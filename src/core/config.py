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
    SENSEVOICE_HOST: str = "127.0.0.1"
    SENSEVOICE_PORT: int = 8800
    WHISPER_APIKEY: str = "something"
    LIBRETRANSLATE_HOST: str = "127.0.0.1"
    LIBRETRANSLATE_PORT: int = 5000
    LIBRETRANSLATE_APIKEY: str = ""
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
    LIMIT_PUBLIC_TEST_USER: Optional[str] = None
    ENABLE_WEB_TRANSLATORS: bool = False
    TRANSLATOR_SERVICE: str = "alibaba"
    TRANSLATOR_SERVICES_LIST: str = "bing,iciba,alibaba,MyMemory,google"  # 翻译供应商列表，按优先级排序
    TRANSLATION_TIMEOUT: float = 1.5  # 翻译请求超时时间（秒）
    LIMITER_REDIS_URL: Optional[str] = "redis://localhost:6379/0"
    TTS_URL: Optional[str] = None
    TTS_TOKEN: Optional[str] = None
    LATEST_VERSION: Optional[str] = None
    PACKAGE_BASE_URL: Optional[str] = None
    PACKAGE_TYPE: Optional[str] = None
    LOG_LEVEL: str = "INFO"  # 日志级别，支持 DEBUG, INFO, WARNING, ERROR, CRITICAL
    ENABLE_TEXT_COMPRESSION: bool = True  # 是否启用文本重复字符压缩
    TEXT_COMPRESSION_MIN_REPEAT: int = 5  # 文本压缩最小重复次数（默认5次）
    

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = True

settings = Settings()

# Construct URLs from settings
WHISPER_URL = f"http://{settings.WHISPER_HOST}:{settings.WHISPER_PORT}/v1/"
SENSEVOICE_URL = f"http://{settings.SENSEVOICE_HOST}:{settings.SENSEVOICE_PORT}/v1/audio/transcriptions"
LOCAL_TRANS_BASE_URL = f"http://{settings.LIBRETRANSLATE_HOST}:{settings.LIBRETRANSLATE_PORT}/"
LOCAL_TRANS_URL = f"{LOCAL_TRANS_BASE_URL}translate"
LOCAL_LANGUAGE_URL = f"{LOCAL_TRANS_BASE_URL}languages"