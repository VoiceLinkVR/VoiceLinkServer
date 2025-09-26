import os
from pydantic_settings import BaseSettings

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
    FLASK_SECRET_KEY: str = "wVddLAF_13dsdddN6XL_QmP.DjkKsV"  # For session middleware
    JWT_ACCESS_TOKEN_EXPIRES_MINUTES: int = 10080  # 7 days
    JWT_ALGORITHM: str = "HS256"
    WHISPER_MODEL: str = "Systran/faster-whisper-large-v3"
    SQL_PATH: str = f"sqlite:///{project_root}/data/db/users.db"
    FILTER_WEB_URL: str | None = "https://raw.githubusercontent.com/VoiceLinkVR/VoiceLinkServer/refs/heads/main/src/filter.json"
    LIMIT_ENABLE: bool = True
    LIMIT_PUBLIC_TEST_USER: str | None = None
    SQLALCHEMY_DATABASE_URL: str = SQL_PATH
    ENABLE_WEB_TRANSLATORS: bool = False
    TRANSLATOR_SERVICE: str = "alibaba"
    LIMITER_REDIS_URL: str | None = "redis://localhost:6379/0"
    TTS_URL: str | None = None
    TTS_TOKEN: str | None = None
    LATEST_VERSION: str | None = None
    PACKAGE_BASE_URL: str | None = None
    PACKAGE_TYPE: str | None = None

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