# -*- coding: utf-8 -*-
"""Crockford Base32 编解码。

选它而不是标准 base32 的原因：字母表去掉了容易看错的 I / L / O / U，
字符集是 0-9 + A-Z 去掉这四个，学生手抄机器码和卡密时不会把 0 抄成 O。

解码端额外容忍常见笔误：I/L → 1，O → 0（Crockford 规范建议的映射）。
"""

from __future__ import annotations

ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

_DECODE_MAP = {c: i for i, c in enumerate(ALPHABET)}
# 常见误读的容错
_DECODE_MAP.update({"I": 1, "L": 1, "O": 0, "U": None})


class Base32Error(ValueError):
    """输入的字符不在字符集内，或长度不足以表示一个字节。"""


def encode(data: bytes) -> str:
    """字节串 → 大写 base32 字符串（长度 = ceil(len*8/5)）。"""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("encode() expects bytes")
    if not data:
        return ""
    value = int.from_bytes(data, "big")
    bits = len(data) * 8
    chars = []
    # 从最高位开始，每 5 位一个字符；不足 5 位的尾部补零
    for shift in range(bits - 5, -5, -5):
        if shift >= 0:
            chars.append(ALPHABET[(value >> shift) & 0x1F])
        else:
            chars.append(ALPHABET[(value << (-shift)) & 0x1F])
    return "".join(chars)


def decode(text: str) -> bytes:
    """base32 字符串 → 字节串。

    大小写不敏感；允许中间有 '-' 或空格分隔。
    尾部补齐的位必须为 0（保证编码唯一），否则抛 Base32Error。
    """
    if not isinstance(text, str):
        raise Base32Error("decode() expects str")
    cleaned = []
    for ch in text.upper():
        if ch in "- _":
            continue
        if ch not in _DECODE_MAP:
            raise Base32Error("非法字符: %r" % ch)
        mapped = _DECODE_MAP[ch]
        if mapped is None:
            raise Base32Error("非法字符: %r" % ch)
        cleaned.append(mapped)
    if not cleaned:
        raise Base32Error("空字符串")

    total_bits = len(cleaned) * 5
    n_bytes = total_bits // 8
    pad = total_bits - n_bytes * 8

    value = 0
    for v in cleaned:
        value = (value << 5) | v

    if pad and (value & ((1 << pad) - 1)) != 0:
        raise Base32Error("尾部补齐位不为零，不是规范编码")
    if n_bytes == 0:
        raise Base32Error("长度不足以表示一个字节")
    return (value >> pad).to_bytes(n_bytes, "big")


def group(text: str, size: int = 4, sep: str = "-") -> str:
    """按固定长度分组显示，便于人读。"""
    return sep.join(text[i:i + size] for i in range(0, len(text), size))
