"""展示记录的序号：时间相同时按记录先后排列，不依赖消息标识。"""

from typing import Any


def transcript_sequences(items: list[dict[str, Any]]) -> list[int]:
    """旧记录没有序号：从后面的已存序号向前补齐，裁掉开头后仍保持原位置。"""
    result = [0] * len(items)
    following = len(items) + 1
    for index in range(len(items) - 1, -1, -1):
        value = items[index].get("sequence")
        following = value if type(value) is int and value > 0 else following - 1
        result[index] = following
    return result


def next_transcript_sequence(items: list[dict[str, Any]]) -> int:
    return transcript_sequences(items)[-1] + 1 if items else 1
