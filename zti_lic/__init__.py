# -*- coding: utf-8 -*-
"""ZTi 授权系统 · 运行时包。

这个包会被打进三个 exe（ZTi智能做题助手 / ZTi手写工具 / ZTiAI接口），
因此遵守三条约束：

  1. **只依赖标准库** —— 不引入 cryptography / PyNaCl，打包零负担；
  2. **不依赖 tkinter** —— ai_api 是 headless 的，UI 由调用方按需注入；
  3. **不向上依赖宿主的任何业务模块** —— 本包可以脱离 pdf-layer 单独测试。

对外最常用的两个入口：

    from zti_lic import machine_code, check
    machine_code()                     # 给用户看的机器码
    status = check("ziti")             # 只查询，不弹窗、不退出
"""

from __future__ import annotations

__version__ = "0.1.0"

from .errors import LicenseError, Reason, message_for  # noqa: E402
from .guard import EXIT_CODE, Status, check, require  # noqa: E402
from .machine import (  # noqa: E402
    MachineCodeError,
    decode_machine_code,
    describe_sources,
    machine_code,
    normalize_code,
)
from .payload import PAYLOAD_VERSION, PRODUCTS  # noqa: E402

__all__ = [
    "__version__",
    "LicenseError",
    "Reason",
    "message_for",
    "machine_code",
    "normalize_code",
    "decode_machine_code",
    "describe_sources",
    "MachineCodeError",
    "PRODUCTS",
    "PAYLOAD_VERSION",
    "Status",
    "check",
    "require",
    "EXIT_CODE",
]
