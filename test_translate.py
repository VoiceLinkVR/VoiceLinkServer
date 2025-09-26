#!/usr/bin/env python3
"""
测试翻译功能的超时和故障转移机制
"""
import os
import sys
import asyncio

# 添加项目路径到Python路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from core.services import do_translate
from core.logging_config import logger

async def test_translation():
    """测试翻译功能"""
    logger.info("开始测试翻译功能...")

    test_cases = [
        ("你好，世界！", "auto", "en"),
        ("Hello, World!", "auto", "zh"),
        ("こんにちは世界！", "auto", "en"),
    ]

    for text, from_lang, to_lang in test_cases:
        logger.info(f"\n测试翻译: '{text}' ({from_lang} -> {to_lang})")
        try:
            result = do_translate(text, from_lang, to_lang)
            logger.info(f"翻译结果: '{result}'")
        except Exception as e:
            logger.error(f"翻译失败: {e}")

        # 等待一下再测试下一个
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(test_translation())