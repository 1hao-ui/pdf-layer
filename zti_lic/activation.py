# -*- coding: utf-8 -*-
"""卡密激活流程（联网）。

协议
----
    POST  {ACTIVATION_URL}/api/activate
    →     {"card": "...", "machine": "...", "product": "...", "version": "..."}

    ← 200 {"ok": true,  "license": {容器}}
    ← 4xx {"ok": false, "reason": "card_used", "message": "这张卡密已在另一台电脑激活"}

客户端拿到 license 后 **必须在本地再验一遍签名** —— 不信任网络返回值。
这样即使 DNS 被劫持、服务端被攻破，攻击者也伪造不出有效授权。
"""

from __future__ import annotations

from . import _endpoint, license as lic, machine, net, store
from ._pubkey import PUBLIC_KEY
from .errors import LicenseError, Reason

__all__ = [
    "activate_with_card",
    "install_from_text",
    "install_from_text_or_card",
    "normalize_card",
    "CardFormatError",
    "CARD_LEN",
]

# 卡密：ZTC + 13 位 Crockford base32 = 16 字符，显示成 4 组
# 前缀用 ZTC（card）而不是 ZTI，避免和机器码（ZTI + 版本位）在视觉上混淆
CARD_LEN = 16
CARD_PREFIX = "ZTC"
_CARD_CHARS = frozenset("0123456789ABCDEFGHJKMNPQRSTVWXYZ")
# 学生手抄常见误读
_CARD_FIX = {"I": "1", "L": "1", "O": "0"}


class CardFormatError(ValueError):
    """卡密格式不合法（长度/字符/前缀）。"""


def normalize_card(card: str) -> str:
    """把用户输入的卡密统一成无分隔大写形式。"""
    if not isinstance(card, str):
        raise CardFormatError("卡密必须是文本")
    cleaned = []
    for ch in card.upper():
        if ch in "- _":
            continue
        ch = _CARD_FIX.get(ch, ch)
        if ch not in _CARD_CHARS:
            raise CardFormatError("卡密里有无法识别的字符：%r" % ch)
        cleaned.append(ch)
    text = "".join(cleaned)
    if len(text) != CARD_LEN:
        raise CardFormatError("卡密长度不对（应为 %d 个字符）" % CARD_LEN)
    if not text.startswith(CARD_PREFIX):
        raise CardFormatError("卡密应以 %s 开头" % CARD_PREFIX)
    return text


def _verify_and_store(doc: dict, product: str, machine_code_str: str) -> dict:
    """本地验签 + 绑定/产品/期限校验，通过后落盘。"""
    validated = lic.validate(doc, product=product,
                             machine_code=machine_code_str,
                             public_key=PUBLIC_KEY)
    store.save_license(doc)
    return validated


def activate_with_card(card: str, product: str,
                       version: str | None = None,
                       timeout: float | None = None) -> dict:
    """用卡密联网激活。

    成功返回授权载荷；失败抛 LicenseError（message 可直接显示给用户）。
    """
    try:
        normalized_card = normalize_card(card)
    except CardFormatError as exc:
        raise LicenseError(Reason.MALFORMED, str(exc)) from exc

    machine_str = machine.machine_code()
    payload = {
        "card": normalized_card,
        "machine": machine_str,
        "product": product,
        "version": version or "",
    }

    result = net.post_json(
        _endpoint.activate_endpoint(), payload,
        timeout=timeout if timeout is not None else _endpoint.TIMEOUT)

    if result.kind == "network":
        raise LicenseError(
            Reason.INTERNAL,
            "无法连接激活服务器（%s）。\n"
            "请检查网络后重试；如果这台电脑不能上网，请联系作者换取离线授权文件。"
            % result.message)

    if not result.ok:
        raise LicenseError(Reason.NOT_LICENSED,
                           result.message or "激活失败，请联系作者。")

    doc = result.get("license")
    if not isinstance(doc, dict):
        raise LicenseError(Reason.MALFORMED, "激活服务器没有返回授权文件")

    return _verify_and_store(doc, product, machine_str)


def install_from_text(text: str, product: str) -> dict:
    """导入一段离线授权码（服务端也可导出的同款容器）。"""
    doc = lic.from_text(text)
    return _verify_and_store(doc, product, machine.machine_code())


def install_from_text_or_card(text: str, product: str,
                              version: str | None = None) -> dict:
    """自动判断用户粘的是"卡密"还是"离线授权码"。

    卡密 → 联网激活；离线授权码 → 本地验签安装。
    这样界面上只需要一个输入框。
    """
    stripped = (text or "").strip()
    if not stripped:
        raise LicenseError(Reason.MALFORMED, "请输入卡密或授权码")

    upper = stripped.upper().replace("\n", "").replace("\r", "")
    if upper.startswith("ZTIL1."):
        return install_from_text(stripped, product)
    return activate_with_card(stripped, product, version=version)
