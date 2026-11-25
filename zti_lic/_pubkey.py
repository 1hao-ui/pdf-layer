# -*- coding: utf-8 -*-
"""内嵌公钥（由 tools/gen_keys.py 生成，请勿手改）。

公钥本来就是公开的，直接写进源码可以省掉一个打包数据文件，
也避免 PyInstaller 下 _MEIPASS 路径找不到的问题。
"""

from __future__ import annotations

PUBLIC_KEY = bytes.fromhex(
    "ee45e35ced6c6c11f7bb0e7859215422e6623a6054da963f421d9ddf75072ead"
)

# 生成时间：2026-11-23
# 对应的私钥在 keys/private.key —— 不在这个仓库的任何发布产物里。
