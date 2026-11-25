# -*- mode: python ; coding: utf-8 -*-
"""ZTi 智能做题助手 —— 单文件(onefile) 打包 spec。

产物：dist/ZTi智能做题助手.exe（单个 exe，运行时自解压到临时目录）。
用于发布「单文件 exe + 7z 分卷」的形式（与仓库发布物一致）。
"""
import os

ICON_PATH = os.path.normpath(os.path.join(SPECPATH, "..", "assets", "icon", "app.ico"))
# 目录一律从 spec 自身位置推导：spec 在 <项目>/源码/pdf-layer/packaging/，
# 上两级是仓库根，再上一级是项目根（字体输出/ 在那里）。
# 不要再写死 C:\Users\... 之类的绝对路径——项目一挪就失效。
APP = os.path.normpath(os.path.join(SPECPATH, os.pardir))
ROOT = os.path.normpath(os.path.join(APP, os.pardir, os.pardir))
FONTS = os.path.join(ROOT, "字体输出")

DATAS = [
    (os.path.join(FONTS, "ZTi-Han"), "字体输出/ZTi-Han"),
    (os.path.join(FONTS, "ZTi-Sym"), "字体输出/ZTi-Sym"),
    (os.path.join(FONTS, "字库索引.json"), "字体输出"),
    (os.path.join(APP, "fonts", "fontcal.json"), "fonts"),
    (os.path.join(APP, "fonts", "fontcal_glyph.json"), "fonts"),
    (os.path.join(APP, "vendor"), "vendor"),
    (os.path.join(APP, "assets"), "assets"),
]

HIDDEN = [
    "mthw", "mthw.math_renderer", "mthw.handwriting", "mthw.config",
    "mthw.latex_parser",
    "fontlib", "fontlib.registry", "fontlib.charsets", "fontlib.template",
    "fontlib.intake", "fontlib.autotune", "fontlib.builder", "fontlib.preview",
]

a = Analysis([os.path.join(APP, "做题窗口.py")], pathex=[APP],
             datas=DATAS, hiddenimports=HIDDEN, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="ZTi智能做题助手", icon=ICON_PATH, debug=False, strip=False, upx=False,
          console=False, onefile=True)
