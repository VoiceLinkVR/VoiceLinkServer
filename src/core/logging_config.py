import logging
import logging.handlers
import os
from contextvars import ContextVar

from core.config import settings

_request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


def set_request_id(request_id: str):
    return _request_id_ctx.set(request_id)


def reset_request_id(token) -> None:
    _request_id_ctx.reset(token)


def get_request_id() -> str:
    return _request_id_ctx.get()


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


def setup_logging():
    """Configure application logging."""
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "logs")
    os.makedirs(log_dir, exist_ok=True)

    log_format = "%(asctime)s - %(name)s - %(levelname)s - [req:%(request_id)s] - %(message)s"
    detailed_format = (
        "%(asctime)s - %(name)s - %(levelname)s - [req:%(request_id)s] - "
        "%(filename)s:%(lineno)d - %(funcName)s() - %(message)s"
    )

    noisy_libraries = [
        "httpx",
        "httpcore",
        "openai",
        "anyio",
        "h11",
    ]
    for lib_name in noisy_libraries:
        logging.getLogger(lib_name).setLevel(logging.INFO)

    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    logger = logging.getLogger()
    logger.setLevel(log_level)

    if logger.handlers:
        logger.handlers.clear()

    request_context_filter = RequestContextFilter()

    console_handler = logging.StreamHandler()
    console_handler.setLevel(log_level)
    console_handler.addFilter(request_context_filter)
    console_handler.setFormatter(logging.Formatter(log_format))

    main_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "server.log"),
        maxBytes=50 * 1024 * 1024,
        backupCount=40,
        encoding="utf-8",
    )
    main_handler.setLevel(log_level)
    main_handler.addFilter(request_context_filter)
    main_handler.setFormatter(logging.Formatter(detailed_format))

    debug_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "debug.log"),
        maxBytes=50 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_handler.addFilter(request_context_filter)
    debug_handler.setFormatter(logging.Formatter(detailed_format))

    error_handler = logging.handlers.RotatingFileHandler(
        os.path.join(log_dir, "error.log"),
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    error_handler.setLevel(logging.ERROR)
    error_handler.addFilter(request_context_filter)
    error_handler.setFormatter(logging.Formatter(detailed_format))

    logger.addHandler(console_handler)
    logger.addHandler(main_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)

    return logger


logger = setup_logging()
