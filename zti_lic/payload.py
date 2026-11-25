# -*- coding: utf-8 -*-
"""授权载荷的规范编码。

为什么要"规范化"
----------------
签名的对象必须是**唯一确定的字节串**，否则服务端签的和客户端算的差一个空格
就会验签失败。所以这里不直接签 `json.dumps(payload)`，而是：

  1. 只保留已知字段（多余字段丢弃，防止夹带）；
  2. 补齐默认值（`expires_at` 缺失按 `None` 处理）；
  3. `sort_keys=True` + `separators=(",",":")` + `ensure_ascii=False` + UTF-8。

这样同一个 payload 无论从文件读、从网络收、还是程序里构造，算出来的字节完全一致。

载荷字段
--------
    v            格式版本，整数，目前 1
    products     产品列表，目前恒为 ["ziti"]；"*" 表示全部（机制保留，暂不使用）
    machine      机器码（规范化后的无分隔大写形式）
    issued_at    签发日期，ISO "YYYY-MM-DD"
    expires_at   到期日期，ISO 或 None（None = 永久）
    max_version  允许的最高软件版本，如 "1.2" 或 "1.x"；None = 不限
    features     功能标记列表，为将来拆价位预留
    serial       授权编号，便于人工对账
"""

from __future__ import annotations

import datetime as _dt
import json
import re

from .errors import LicenseError, Reason
from .machine import canonical_code

__all__ = [
    "PAYLOAD_VERSION",
    "PRODUCTS",
    "normalize",
    "canonical_bytes",
    "parse_date",
    "version_key",
]

PAYLOAD_VERSION = 1

# 授权单元。
#
# 1.1 及以前按三个 exe 各自授权（ziti_quiz / ziti_write / ziti_api）。
# 1.2 起整个产品就是一个 exe、一份授权：PRODUCT 是唯一的授权单元，
# 授权文件里的 products 只会出现 ["ziti"]。
#
# 多产品的机制（products 是列表、支持 "*" 通配）仍然保留，只是不再使用，
# 将来真要拆价位不用改格式。
PRODUCT = "ziti"
PRODUCTS = (PRODUCT,)

_FIELDS = (
    "v", "products", "machine", "issued_at", "expires_at",
    "max_version", "features", "serial",
)

_DEFAULTS = {
    "v": PAYLOAD_VERSION,
    "expires_at": None,
    "max_version": None,
    "features": [],
    "serial": "",
}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def parse_date(value) -> _dt.date | None:
    """解析 ISO 日期；None / 空串返回 None，格式不对抛 LicenseError。"""
    if value is None or value == "":
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if not isinstance(value, str) or not _DATE_RE.match(value.strip()):
        raise LicenseError(Reason.MALFORMED, "授权里的日期格式不正确：%r" % (value,))
    try:
        return _dt.date.fromisoformat(value.strip())
    except ValueError as exc:
        raise LicenseError(Reason.MALFORMED, "授权里的日期不合法：%r" % (value,)) from exc


def version_key(text: str) -> tuple:
    """把版本号转成可比较的元组。

    "1.2.3" → (1,2,3)；"1.x" → (1,)（x 视为通配，只比较前面的数字段）。
    无法解析时返回 ()，调用方按"不限制"处理。
    """
    if not text:
        return ()
    parts = []
    for chunk in str(text).split("."):
        chunk = chunk.strip()
        match = re.match(r"^(\d+)", chunk)
        if not match:
            break
        parts.append(int(match.group(1)))
        if match.end() != len(chunk):
            break
    return tuple(parts)


def _as_str_list(value, field: str) -> list:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        raise LicenseError(Reason.MALFORMED, "字段 %s 应为列表" % field)
    out = []
    for item in value:
        if not isinstance(item, str):
            raise LicenseError(Reason.MALFORMED, "字段 %s 里有非字符串项" % field)
        out.append(item)
    return out


def normalize(payload: dict) -> dict:
    """校验并规范化载荷，返回只含已知字段的新字典。

    任何结构性问题都抛 LicenseError(Reason.MALFORMED)。
    """
    if not isinstance(payload, dict):
        raise LicenseError(Reason.MALFORMED, "授权内容是空的或格式不对")

    result = dict(_DEFAULTS)

    version = payload.get("v", PAYLOAD_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        raise LicenseError(Reason.MALFORMED, "授权格式版本号非法")
    if version > PAYLOAD_VERSION:
        raise LicenseError(Reason.UNSUPPORTED_VERSION)
    result["v"] = version

    products = _as_str_list(payload.get("products"), "products")
    if not products:
        raise LicenseError(Reason.MALFORMED, "授权里没有包含任何产品")
    result["products"] = products

    machine = payload.get("machine")
    if not isinstance(machine, str) or not machine.strip():
        raise LicenseError(Reason.MALFORMED, "授权里缺少机器码")
    # 统一去掉手工加的 '-' / 空格，避免签端与验端算出不同字节
    result["machine"] = canonical_code(machine)
    if not result["machine"]:
        raise LicenseError(Reason.MALFORMED, "授权里的机器码为空")

    issued = payload.get("issued_at")
    if issued is None or issued == "":
        issued = _dt.date.today().isoformat()
    result["issued_at"] = parse_date(issued).isoformat()

    result["expires_at"] = parse_date(payload.get("expires_at")
                                      if "expires_at" in payload
                                      else result["expires_at"])
    if result["expires_at"] is not None:
        result["expires_at"] = result["expires_at"].isoformat()

    max_version = payload.get("max_version")
    if max_version is not None and not isinstance(max_version, str):
        raise LicenseError(Reason.MALFORMED, "字段 max_version 应为字符串")
    result["max_version"] = max_version or None

    result["features"] = _as_str_list(payload.get("features"), "features")

    serial = payload.get("serial") or ""
    if not isinstance(serial, str):
        raise LicenseError(Reason.MALFORMED, "字段 serial 应为字符串")
    result["serial"] = serial

    return result


def canonical_bytes(payload: dict) -> bytes:
    """规范化后的字节串 —— 这就是被签名的内容。"""
    normalized = normalize(payload)
    text = json.dumps(normalized, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)
    return text.encode("utf-8")
