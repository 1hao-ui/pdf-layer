# -*- coding: utf-8 -*-
"""授权文件的构造、验签与校验。

容器格式
--------
    {
      "zti_license": 1,                  ← 容器格式版本（与载荷的 v 无关）
      "payload": { ... },                ← 被签名的内容，见 payload.py
      "sig": "<base64 Ed25519 签名>"      ← 对 canonical_bytes(payload) 的签名
    }

客户端只做三件事：读容器 → 用内嵌公钥验签 → 校验机器码 / 产品 / 期限。
签发（build）只在服务端和开发者本机调用。
"""

from __future__ import annotations

import base64
import binascii
import datetime as _dt
import json
import zlib

from . import _b32, _ed25519, machine as machine_mod, payload as payload_mod
from .errors import LicenseError, Reason

__all__ = [
    "CONTAINER_KEY",
    "CONTAINER_VERSION",
    "build",
    "verify",
    "validate",
    "dump_json",
    "load_json",
    "to_text",
    "from_text",
    "is_license_doc",
]

CONTAINER_KEY = "zti_license"
CONTAINER_VERSION = 1

_TEXT_PREFIX = "ZTIL1."


# ---------------------------------------------------------------------------
# 构造（仅签发端）
# ---------------------------------------------------------------------------

def build(payload: dict, private_key: bytes) -> dict:
    """用私钥签发一份授权容器。

    payload 会先过 normalize，额外字段不会进入签名。
    """
    normalized = payload_mod.normalize(payload)
    message = payload_mod.canonical_bytes(normalized)
    signature = _ed25519.sign(message, private_key)
    return {
        CONTAINER_KEY: CONTAINER_VERSION,
        "payload": normalized,
        "sig": base64.b64encode(signature).decode("ascii"),
    }


# ---------------------------------------------------------------------------
# 读取
# ---------------------------------------------------------------------------

def is_license_doc(doc) -> bool:
    """粗略判断是不是授权容器（不做密码学校验）。"""
    return isinstance(doc, dict) and CONTAINER_KEY in doc and "payload" in doc


def dump_json(doc: dict) -> str:
    """序列化成便于人看和 diff 的 JSON 文本。"""
    return json.dumps(doc, ensure_ascii=False, indent=2, sort_keys=True)


def load_json(text: str) -> dict:
    """解析授权 JSON 文本。"""
    try:
        doc = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise LicenseError(Reason.MALFORMED, "授权文件不是合法的 JSON") from exc
    if not isinstance(doc, dict):
        raise LicenseError(Reason.MALFORMED, "授权文件内容不是对象")
    return doc


def to_text(doc: dict) -> str:
    """授权容器 → 单行文本（zlib + Crockford base32）。

    用于断网用户手工传递：整段可以塞进微信，粘进软件即可。
    """
    raw = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _TEXT_PREFIX + _b32.encode(zlib.compress(raw, 9))


def from_text(text: str) -> dict:
    """单行文本 → 授权容器。"""
    if not isinstance(text, str):
        raise LicenseError(Reason.MALFORMED, "授权码必须是文本")
    cleaned = "".join(text.split())
    if not cleaned.upper().startswith(_TEXT_PREFIX):
        raise LicenseError(Reason.MALFORMED, "这不是本软件的授权码")
    body = cleaned[len(_TEXT_PREFIX):]
    try:
        packed = _b32.decode(body)
    except _b32.Base32Error as exc:
        raise LicenseError(Reason.MALFORMED, "授权码里有无法识别的字符") from exc
    try:
        raw = zlib.decompress(packed)
    except zlib.error as exc:
        raise LicenseError(Reason.MALFORMED, "授权码已损坏") from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise LicenseError(Reason.MALFORMED, "授权码内容无法解析") from exc
    if not isinstance(doc, dict):
        raise LicenseError(Reason.MALFORMED, "授权码内容不是对象")
    return doc


# ---------------------------------------------------------------------------
# 验签与校验（客户端）
# ---------------------------------------------------------------------------

def verify(doc: dict, public_key: bytes) -> dict:
    """验签并返回规范化载荷。失败抛 LicenseError。"""
    if not isinstance(doc, dict):
        raise LicenseError(Reason.MALFORMED, "授权内容是空的或格式不对")
    if CONTAINER_KEY not in doc:
        raise LicenseError(Reason.MALFORMED, "这不像是本软件的授权文件")

    container_version = doc.get(CONTAINER_KEY)
    if not isinstance(container_version, int) or isinstance(container_version, bool):
        raise LicenseError(Reason.MALFORMED, "授权文件格式版本非法")
    if container_version > CONTAINER_VERSION:
        raise LicenseError(Reason.UNSUPPORTED_VERSION)

    raw_payload = doc.get("payload")
    if not isinstance(raw_payload, dict):
        raise LicenseError(Reason.MALFORMED, "授权文件里没有授权内容")

    sig_text = doc.get("sig")
    if not isinstance(sig_text, str) or not sig_text:
        raise LicenseError(Reason.MALFORMED, "授权文件里没有签名")
    try:
        signature = base64.b64decode(sig_text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError(Reason.MALFORMED, "签名不是合法的 base64") from exc

    # 先规范化再算签名内容：顺序与签端完全一致
    normalized = payload_mod.normalize(raw_payload)
    message = payload_mod.canonical_bytes(normalized)

    if not _ed25519.verify(signature, message, public_key):
        raise LicenseError(Reason.BAD_SIGNATURE)

    return normalized


def validate(doc: dict,
             *,
             product: str,
             machine_code: str,
             public_key: bytes,
             app_version: str | None = None,
             today: _dt.date | None = None) -> dict:
    """完整校验：验签 → 机器码 → 产品 → 期限 → 版本上限。

    全部通过返回载荷字典；任一项失败抛 LicenseError。
    """
    normalized = verify(doc, public_key)
    today = today or _dt.date.today()

    # 机器码必须对上 —— 这是防复制的关键，靠文件位置防不住
    if normalized["machine"] != _norm(machine_code):
        raise LicenseError(Reason.WRONG_MACHINE)

    products = normalized["products"]
    if product not in products and "*" not in products:
        raise LicenseError(Reason.NOT_LICENSED, app=product_display_name(product))

    expires_at = payload_mod.parse_date(normalized.get("expires_at"))
    if expires_at is not None and today > expires_at:
        raise LicenseError(Reason.EXPIRED, date=expires_at.isoformat())

    issued_at = payload_mod.parse_date(normalized.get("issued_at"))
    if issued_at is not None and issued_at > today + _dt.timedelta(days=1):
        # 签发日期在今天之后：许可文件可能被改了系统时间，但不阻断，
        # 只当作异常记录交给上层决定（这里不抛，避免误伤时钟不准的机器）
        pass

    max_version = normalized.get("max_version")
    if max_version and app_version:
        allowed = payload_mod.version_key(max_version)
        current = payload_mod.version_key(app_version)
        if allowed and current and _compare_prefix(current, allowed) > 0:
            raise LicenseError(Reason.VERSION_TOO_NEW, ver=max_version)

    return normalized


def _norm(code) -> str:
    if not isinstance(code, str):
        return ""
    return machine_mod.canonical_code(code)


def _compare_prefix(current: tuple, allowed: tuple) -> int:
    """比较版本前缀：只比 allowed 覆盖到的段数。"""
    for a, b in zip(current, allowed):
        if a != b:
            return 1 if a > b else -1
    return 0


def product_display_name(product: str) -> str:
    return {
        "ziti": "ZTi 智能做题助手",
        # 1.1 及以前的三个产品名，保留以便识别旧授权
        "ziti_quiz": "ZTi 智能做题助手",
        "ziti_write": "ZTi 手写工具",
        "ziti_api": "ZTi AI 接口",
    }.get(product, product)
