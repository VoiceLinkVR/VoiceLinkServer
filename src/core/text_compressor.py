"""
Text compression utilities.

Provides repeated character and repeated phrase compression helpers.
"""
import re


class TextCompressor:
    """Compress repeated characters and repeated phrases."""

    def __init__(self, min_repeat_count: int = 5, min_word_length: int = 2):
        """
        Initialize a text compressor.

        Args:
            min_repeat_count: Minimum repetition count before compression.
            min_word_length: Minimum phrase length for repetition matching.
        """
        self.min_repeat_count = min_repeat_count
        self.min_word_length = min_word_length
        self.char_pattern = re.compile(rf"(.)\1{{{min_repeat_count - 1},}}")
        self.punctuation = r"[，。、；：！？,\.;:!\?·\-\u3001\u3002\u060C\u061B\u061F\u0964\u0965]"

    def compress(self, text: str) -> str:
        """
        Compress repeated characters and repeated phrases in text.

        Args:
            text: Input text.

        Returns:
            Compressed text.
        """
        if not text:
            return text

        result = self._compress_punctuated_repetitions(text)

        def replace_char_match(match):
            char = match.group(1)
            count = len(match.group(0))
            return f"{char}*{count}"

        result = self.char_pattern.sub(replace_char_match, result)
        result = self._compress_word_repetitions(result)
        return result

    def _compress_punctuated_repetitions(self, text: str) -> str:
        """Compress repeated phrases separated by punctuation."""
        for word_len in range(self.min_word_length, 10):
            pattern = rf"((.{{{word_len}}}){self.punctuation})\1{{{self.min_repeat_count - 2},}}\2?"

            def replace_match(match):
                full_match = match.group(0)
                word = match.group(2)
                count = full_match.count(word)
                if count >= self.min_repeat_count:
                    return f"{word}*{count}"
                return full_match

            text = re.sub(pattern, replace_match, text)

        return text

    def _compress_word_repetitions(self, text: str) -> str:
        """Compress repeated words or repeated contiguous phrases."""
        if " " in text:
            word_pattern = re.compile(rf"\b(\S{{{self.min_word_length},}})(?:\s+\1){{{self.min_repeat_count - 1},}}\b")

            def replace_word_match(match):
                word = match.group(1)
                full_match = match.group(0)
                count = len(full_match.split())
                return f"{word}*{count}"

            result = word_pattern.sub(replace_word_match, text)

            parts = []
            for part in result.split():
                if "*" not in part:
                    parts.append(self._compress_continuous_repetition(part))
                else:
                    parts.append(part)
            return " ".join(parts)

        return self._compress_continuous_repetition(text)

    def _compress_continuous_repetition(self, text: str) -> str:
        """Compress contiguous repeated substrings."""
        if len(text) < self.min_word_length * self.min_repeat_count:
            return text

        for pattern_length in range(self.min_word_length, len(text) // self.min_repeat_count + 1):
            pattern = rf"(.{{{pattern_length}}})\1{{{self.min_repeat_count - 1},}}"
            regex = re.compile(pattern)

            def replace_match(match):
                matched_pattern = match.group(1)
                full_match = match.group(0)
                repeat_count = len(full_match) // len(matched_pattern)
                return f"{matched_pattern}*{repeat_count}"

            new_text = regex.sub(replace_match, text)
            if new_text != text:
                return new_text

        return text

    def compress_batch(self, texts: list[str]) -> list[str]:
        """Compress a list of texts."""
        return [self.compress(text) for text in texts]


default_compressor = TextCompressor(min_repeat_count=5, min_word_length=2)


def compress_repeated_chars(text: str, min_repeat_count: int = 5, min_word_length: int = 2) -> str:
    """
    Convenience wrapper for repeated text compression.

    Args:
        text: Input text.
        min_repeat_count: Minimum repetition count before compression.
        min_word_length: Minimum phrase length for repetition matching.

    Returns:
        Compressed text.
    """
    if min_repeat_count == 5 and min_word_length == 2:
        return default_compressor.compress(text)

    compressor = TextCompressor(min_repeat_count, min_word_length)
    return compressor.compress(text)
