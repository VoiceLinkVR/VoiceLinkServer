import os
import logging
import logging.handlers
from datetime import datetime

def setup_logging():
    """配置详细日志系统"""
    # 创建logs目录（如果不存在）
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'logs')
    os.makedirs(log_dir, exist_ok=True)

    # 配置日志格式
    log_format = '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    detailed_format = '%(asctime)s - %(name)s - %(levelname)s - %(filename)s:%(lineno)d - %(funcName)s() - %(message)s'

    # 创建logger
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_formatter = logging.Formatter(log_format)
    console_handler.setFormatter(console_formatter)

    # 文件处理器 - 主要日志
    main_log_file = os.path.join(log_dir, 'server.log')
    main_handler = logging.handlers.RotatingFileHandler(
        main_log_file, maxBytes=50*1024*1024, backupCount=40, encoding='utf-8'
    )
    main_handler.setLevel(logging.INFO)
    main_formatter = logging.Formatter(detailed_format)
    main_handler.setFormatter(main_formatter)

    # 文件处理器 - Debug日志
    debug_log_file = os.path.join(log_dir, 'debug.log')
    debug_handler = logging.handlers.RotatingFileHandler(
        debug_log_file, maxBytes=50*1024*1024, backupCount=5, encoding='utf-8'
    )
    debug_handler.setLevel(logging.DEBUG)
    debug_formatter = logging.Formatter(detailed_format)
    debug_handler.setFormatter(debug_formatter)

    # 错误日志处理器
    error_log_file = os.path.join(log_dir, 'error.log')
    error_handler = logging.handlers.RotatingFileHandler(
        error_log_file, maxBytes=20*1024*1024, backupCount=5, encoding='utf-8'
    )
    error_handler.setLevel(logging.ERROR)
    error_formatter = logging.Formatter(detailed_format)
    error_handler.setFormatter(error_formatter)

    # 添加处理器到logger
    logger.addHandler(console_handler)
    logger.addHandler(main_handler)
    logger.addHandler(debug_handler)
    logger.addHandler(error_handler)

    return logger

# 初始化日志系统
logger = setup_logging()