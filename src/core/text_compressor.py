"""
文本压缩工具模块
提供文本重复字符和词组压缩功能
"""
import re
from typing import Optional, List


class TextCompressor:
    """文本压缩器"""

    def __init__(self, min_repeat_count: int = 5, min_word_length: int = 2):
        """
        初始化文本压缩器

        Args:
            min_repeat_count: 最小重复次数（默认5次才压缩）
            min_word_length: 最小词组长度（默认2个字符）
        """
        self.min_repeat_count = min_repeat_count
        self.min_word_length = min_word_length
        # 预编译正则表达式以提高性能
        # 匹配连续的相同字符（单字符重复）
        self.char_pattern = re.compile(rf'(.)\1{{{min_repeat_count-1},}}')

    def compress(self, text: str) -> str:
        """
        压缩文本中的重复字符和词组

        Args:
            text: 输入的文本

        Returns:
            压缩后的文本
        """
        if not text:
            return text

        # 第一步：压缩单字符重复
        def replace_char_match(match):
            char = match.group(1)
            count = len(match.group(0))
            return f"{char}*{count}"

        result = self.char_pattern.sub(replace_char_match, text)

        # 第二步：压缩词组重复
        result = self._compress_word_repetitions(result)

        return result

    def _compress_word_repetitions(self, text: str) -> str:
        """
        压缩词组重复（支持中英文混合）
        """
        # 先处理有空格的部分（英文）
        if ' ' in text:
            # 使用正则表达式找到所有连续的相同单词
            word_pattern = re.compile(rf'\b(\S{{{self.min_word_length},}})(?:\s+\1){{{self.min_repeat_count-1},}}\b')

            def replace_word_match(match):
                word = match.group(1)
                # 计算重复次数
                full_match = match.group(0)
                count = len(full_match.split())
                return f"{word}*{count}"

            result = word_pattern.sub(replace_word_match, text)

            # 处理剩余的中文部分（没有空格的连续字符）
            parts = []
            for part in result.split():
                if '*' not in part:  # 如果已经被压缩过，不再处理
                    compressed = self._compress_continuous_repetition(part)
                    parts.append(compressed)
                else:
                    parts.append(part)
            return ' '.join(parts)
        else:
            # 无空格的中文文本
            return self._compress_continuous_repetition(text)

    def _compress_continuous_repetition(self, text: str) -> str:
        """
        压缩连续重复的字符串（适用于中文）
        """
        if len(text) < self.min_word_length * self.min_repeat_count:
            return text

        # 尝试找到重复的子串
        for pattern_length in range(self.min_word_length, len(text) // self.min_repeat_count + 1):
            # 检查前pattern_length个字符是否构成重复模式
            pattern = text[:pattern_length]
            repeat_count = 1

            # 计算完整重复的次数
            while len(text) >= pattern_length * (repeat_count + 1):
                next_segment = text[pattern_length * repeat_count:pattern_length * (repeat_count + 1)]
                if next_segment == pattern:
                    repeat_count += 1
                else:
                    break

            # 如果找到足够次数的重复，进行压缩
            if repeat_count >= self.min_repeat_count:
                # 检查是否整个字符串都是这个模式的重复
                if pattern_length * repeat_count == len(text):
                    return f"{pattern}*{repeat_count}"
                else:
                    # 部分重复，处理剩余部分
                    compressed = f"{pattern}*{repeat_count}"
                    remaining = text[pattern_length * repeat_count:]
                    # 递归处理剩余部分
                    remaining_compressed = self._compress_continuous_repetition(remaining)
                    if remaining_compressed:
                        return compressed + remaining_compressed
                    else:
                        return compressed

        # 没有找到重复模式，返回原文
        return text

    def compress_batch(self, texts: list[str]) -> list[str]:
        """
        批量压缩文本

        Args:
            texts: 文本列表

        Returns:
            压缩后的文本列表
        """
        return [self.compress(text) for text in texts]


# 全局默认实例
default_compressor = TextCompressor(min_repeat_count=5, min_word_length=2)


def compress_repeated_chars(text: str, min_repeat_count: int = 5, min_word_length: int = 2) -> str:
    """
    压缩文本中的重复字符和词组（便捷函数）

    Args:
        text: 输入的文本
        min_repeat_count: 最小重复次数（默认5次才压缩）
        min_word_length: 最小词组长度（默认2个字符）

    Returns:
        压缩后的文本
    """
    if min_repeat_count == 5 and min_word_length == 2:
        # 使用默认实例以提高性能
        return default_compressor.compress(text)
    else:
        # 创建临时实例
        compressor = TextCompressor(min_repeat_count, min_word_length)
        return compressor.compress(text)