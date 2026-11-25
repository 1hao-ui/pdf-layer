# -*- coding: utf-8 -*-
"""错误码与人话文案。

设计原则（对应 README 硬性要求 #3）：
  - 面向用户的永远是 message 字段，中文、说清楚下一步该做什么；
  - reason 是给开发者和日志看的机器码，不出现在界面上；
  - 模块本身不 print、不弹窗，UI 由调用方决定。
"""

from __future__ import annotations

from enum import Enum


class Reason(str, Enum):
    """失败原因机器码。字符串枚举，方便直接写日志和单测。"""

    OK = "ok"

    # ---- 授权文件本身 ----
    NO_LICENSE = "no_license"              # 两处都没找到授权文件
    MALFORMED = "malformed"                # 不是合法 JSON / 结构不对
    BAD_SIGNATURE = "bad_signature"        # 验签失败（被改过或伪造）
    UNSUPPORTED_VERSION = "unsupported_version"   # 授权格式版本比本程序新

    # ---- 授权内容 ----
    WRONG_MACHINE = "wrong_machine"        # 机器码对不上（换机或拷来的）
    NOT_LICENSED = "not_licensed"          # 授权不含本产品
    EXPIRED = "expired"                    # 已过有效期
    VERSION_TOO_NEW = "version_too_new"    # 软件版本超出授权上限

    # ---- 试用 ----
    TRIAL = "trial"                        # 试用期内，可用
    TRIAL_EXPIRED = "trial_expired"        # 试用已到期

    # ---- 内部 ----
    INTERNAL = "internal"                  # 采集指纹等内部异常


# 文案表。{app} 由调用方传入产品显示名。
_MESSAGES = {
    Reason.NO_LICENSE: "未找到授权文件。",
    Reason.MALFORMED: "授权文件已损坏，无法读取。",
    Reason.BAD_SIGNATURE: "授权文件校验未通过（可能被修改过）。",
    Reason.UNSUPPORTED_VERSION: "授权文件版本过新，请先更新本软件。",
    Reason.WRONG_MACHINE: "本授权绑定的是另一台电脑，无法在这台电脑上使用。",
    Reason.NOT_LICENSED: "本授权不包含「{app}」的使用权。",
    Reason.EXPIRED: "授权已于 {date} 到期。",
    Reason.VERSION_TOO_NEW: "本授权最高支持到 {ver}，当前版本超出了范围。",
    Reason.TRIAL: "试用还剩 {days} 天。",
    Reason.TRIAL_EXPIRED: "试用期已结束。",
    Reason.INTERNAL: "授权校验出现内部错误。",
    Reason.OK: "授权正常。",
}


class LicenseError(Exception):
    """授权校验失败。message 是可以直接显示给用户的中文。"""

    def __init__(self, reason: Reason, message: str = "", **fmt):
        self.reason = reason
        if not message:
            template = _MESSAGES.get(reason, "授权校验失败。")
            message = template.format(**fmt) if fmt else template
        self.message = message
        super().__init__(message)

    def __str__(self) -> str:  # pragma: no cover - 便于日志
        return "%s: %s" % (self.reason.value, self.message)


def message_for(reason: Reason, **fmt) -> str:
    """取某原因对应的中文文案。"""
    template = _MESSAGES.get(reason, "授权校验失败。")
    try:
        return template.format(**fmt) if fmt else template
    except (KeyError, IndexError):
        return template
