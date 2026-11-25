# -*- coding: utf-8 -*-
"""纯标准库 Ed25519 实现（RFC 8032）。

为什么不用 cryptography / PyNaCl
--------------------------------
目标环境是 Windows 10/11 + Python 3.14 + PyInstaller。多引一个带二进制的密码库
就多三份风险：wheel 对 3.14 的适配、PyInstaller 的 DLL 收集、杀软误报。而授权
场景只需要「签发」和「验签」两个动作，Ed25519 用一百多行大整数运算就能实现，
并且能逐字节通过 RFC 8032 §7.1 的官方测试向量（见 tests/test_ed25519.py）。

安全边界
--------
本模块不追求常数时间。授权校验面对的是本机用户，本地计时侧信道没有意义；
真正的边界是 **没有私钥就算不出签名**，这一点由算法本身保证。

对外接口
--------
    generate_private_key() -> bytes           # 32 字节随机私钥
    public_key(sk) -> bytes                   # 32 字节公钥
    sign(message, sk) -> bytes                # 64 字节签名（R || S）
    verify(signature, message, pk) -> bool    # 永不抛异常，出错一律 False
"""

from __future__ import annotations

import hashlib
import os

__all__ = [
    "generate_private_key",
    "public_key",
    "sign",
    "verify",
    "PUBLIC_KEY_SIZE",
    "SIGNATURE_SIZE",
]

# ---- 曲线参数（RFC 8032 §5.1）---------------------------------------------
# p = 2**255 - 19，q 为基点的阶
p = 2**255 - 19
q = 2**252 + 27742317777372353535851937790883648493
# d = -121665 / 121666 mod p
_d = (-121665 * pow(121666, p - 2, p)) % p
# sqrt(-1) mod p，用于解出点坐标
_I = pow(2, (p - 1) // 4, p)

PUBLIC_KEY_SIZE = 32
SIGNATURE_SIZE = 64
_PRIVATE_KEY_SIZE = 32


def _inv(x: int) -> int:
    """模 p 求逆。"""
    return pow(x, p - 2, p)


def _xrecover(y: int) -> int:
    """由 y 还原曲线上的 x（RFC 8032 §5.1.3）。"""
    xx = (y * y - 1) * _inv(_d * y * y + 1)
    x = pow(xx, (p + 3) // 8, p)
    if (x * x - xx) % p != 0:
        x = (x * _I) % p
    if x % 2 != 0:
        x = p - x
    return x


def _recover_base_point() -> tuple[int, int, int, int]:
    """还原标准基点 B（扩展坐标：x, y, z, t）。"""
    by = (4 * _inv(5)) % p
    bx = _xrecover(by)
    return (bx, by, 1, (bx * by) % p)


# 基点，模块加载时算一次；之后所有标量乘都从它出发
_B = _recover_base_point()
_IDENTITY = (0, 1, 1, 0)


def _edwards_add(P, Q):
    """扩展坐标下的点加（RFC 8032 §5.1.4 的 edwards 公式）。"""
    x1, y1, z1, t1 = P
    x2, y2, z2, t2 = Q
    a = (y1 - x1) * (y2 - x2) % p
    b = (y1 + x1) * (y2 + x2) % p
    c = t1 * 2 * _d * t2 % p
    dd = z1 * 2 * z2 % p
    e = b - a
    f = dd - c
    g = dd + c
    h = b + a
    return (e * f % p, g * h % p, f * g % p, e * h % p)


def _scalar_mult(P, e: int):
    """标量乘 e*P（double-and-add）。e 为任意非负整数。"""
    result = _IDENTITY
    addend = P
    while e > 0:
        if e & 1:
            result = _edwards_add(result, addend)
        addend = _edwards_add(addend, addend)
        e >>= 1
    return result


def _encode_point(P) -> bytes:
    """点 → 32 字节压缩编码（低 255 位是 y，最高位是 x 的奇偶）。"""
    x, y, z, _t = P
    zi = _inv(z)
    x = x * zi % p
    y = y * zi % p
    value = y | ((x & 1) << 255)
    return value.to_bytes(32, "little")


def _decode_point(s: bytes):
    """32 字节压缩编码 → 点。非规范编码或不在曲线上时抛 ValueError。"""
    if len(s) != 32:
        raise ValueError("point encoding must be 32 bytes")
    value = int.from_bytes(s, "little")
    sign_bit = value >> 255
    y = value & ((1 << 255) - 1)
    # 拒绝非规范的 y（RFC 8032 §5.1.3：y 必须 < p）
    if y >= p:
        raise ValueError("non-canonical point encoding")
    x = _xrecover(y)
    if (x & 1) != sign_bit:
        x = p - x
    # 验证确实在曲线上：-x^2 + y^2 = 1 + d*x^2*y^2
    if (-x * x + y * y - 1 - _d * (x * y % p) ** 2) % p != 0:
        raise ValueError("point is not on curve")
    return (x, y, 1, (x * y) % p)


def _sha512(data: bytes) -> bytes:
    return hashlib.sha512(data).digest()


def _challenge(*parts: bytes) -> int:
    """SHA-512 后取小端整数并对 q 取模。

    相当于 RFC 的 hint()；对 q 取模是等价的，因为基点 B 的阶就是 q，
    但能把标量从 512 位降到 253 位，标量乘快一倍。
    """
    h = hashlib.sha512()
    for part in parts:
        h.update(part)
    return int.from_bytes(h.digest(), "little") % q


def _expand_seed(sk: bytes) -> tuple[int, bytes]:
    """私钥种子 → (标量 a, 后半段 prefix)。等价于 RFC 的 clamping。"""
    if len(sk) != _PRIVATE_KEY_SIZE:
        raise ValueError("private key must be 32 bytes")
    h = _sha512(sk)
    # a = 2^254 + (h[0:32] 的小端值，清低 3 位和最高 2 位)
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def generate_private_key() -> bytes:
    """生成 32 字节随机私钥种子。"""
    return os.urandom(_PRIVATE_KEY_SIZE)


def public_key(sk: bytes) -> bytes:
    """由私钥推导公钥（32 字节）。"""
    a, _prefix = _expand_seed(sk)
    return _encode_point(_scalar_mult(_B, a))


def sign(message: bytes, sk: bytes) -> bytes:
    """对 message 签名，返回 64 字节（R || S）。"""
    a, prefix = _expand_seed(sk)
    pk = _encode_point(_scalar_mult(_B, a))
    r = _challenge(prefix, message)
    R = _scalar_mult(_B, r)
    R_enc = _encode_point(R)
    S = (r + _challenge(R_enc, pk, message) * a) % q
    return R_enc + S.to_bytes(32, "little")


def verify(signature: bytes, message: bytes, pk: bytes) -> bool:
    """校验签名。任何异常、长度不对、编码不规范一律返回 False。"""
    try:
        if len(signature) != SIGNATURE_SIZE or len(pk) != PUBLIC_KEY_SIZE:
            return False
        if not isinstance(signature, (bytes, bytearray)) or not isinstance(pk, (bytes, bytearray)):
            return False
        R = _decode_point(bytes(signature[:32]))
        A = _decode_point(bytes(pk))
        S = int.from_bytes(bytes(signature[32:]), "little")
        # S 必须落在 [0, q)，否则存在可延展性
        if S >= q:
            return False
        k = _challenge(bytes(signature[:32]), bytes(pk), message)
        # [S]B == R + [k]A
        left = _encode_point(_scalar_mult(_B, S))
        right = _encode_point(_edwards_add(R, _scalar_mult(A, k)))
        return left == right
    except Exception:
        return False
