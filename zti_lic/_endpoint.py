# -*- coding: utf-8 -*-
"""激活服务端地址。

部署好签发/激活服务后，把下面的 ACTIVATION_URL 改成你的地址即可。
也可以在运行时用环境变量 ZTI_ACTIVATION_URL 覆盖（方便内网测试和切换环境）。

协议见 server/README 与 activation.py 里的说明。
"""

from __future__ import annotations

import os

# ⚠️ 部署后必须改这里。客户端会往 ACTIVATION_URL + "/api/activate" 发 POST。
ACTIVATION_URL = "http://127.0.0.1:8787"

# 单次激活请求超时（秒）。网络差时宁可早点失败给出提示，也不要让启动界面卡住。
TIMEOUT = 8.0


def activation_url() -> str:
    """当前生效的服务端地址（环境变量优先）。"""
    return (os.environ.get("ZTI_ACTIVATION_URL") or ACTIVATION_URL).rstrip("/")


def activate_endpoint() -> str:
    return activation_url() + "/api/activate"
