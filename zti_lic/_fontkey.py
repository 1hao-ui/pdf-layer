# -*- coding: utf-8 -*-
"""字体加密用的密钥（由 tools/gen_font_key.py 生成，请勿手改）。

作用：打包时把 ZTi-Han / ZTi-Sym 的手写字体加密成一个数据块，
运行时在**授权校验通过之后**才解密还原，从而做到：

    · 拷走整个文件夹 → 里面只有加密块，字体用不了
    · 没激活 / 试用到期 → 字体不解密，等于没有手写库

诚实说明
--------
密钥必须随程序分发（程序自己要解密），所以它挡得住"拷走就用"，
挡不住会逆向 exe 的人。这与 02 决策清单"够用即可"的定位一致。

换密钥会让**已发布的版本无法解密**，只能连程序一起更新，请勿随意更换。
"""

from __future__ import annotations

FONT_KEY = bytes.fromhex(
    "ec5c09673c8a96acbf9dce9b0d404c2a8db16d7bbd9924920f499dc71a92adaa"
)

# 加密块的文件名（放在「字体输出」目录里，与字体同一层）
BLOB_NAME = "ziti_fonts.zfp"

# 加密块格式版本，改格式时 +1
BLOB_VERSION = 1
