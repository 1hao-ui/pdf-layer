# -*- mode: python ; coding: utf-8 -*-
"""ZTi 智能做题助手 —— 单文件(onefile) 打包 spec。

产物：dist/ZTi智能做题助手.exe（单个 exe，运行时自解压到临时目录）。
用于发布「单文件 exe + 7z 分卷」的形式（与仓库发布物一致）。
"""
import os

ICON_PATH = os.path.normpath(os.path.join(SPECPATH, "..", "assets", "icon", "app.ico"))
ROOT = r"C:\Users\a\Desktop\手写体生成器"
APP = os.path.join(ROOT, "_refs", "pdf-layer")
FONTS = os.path.join(ROOT, "字体输出")

DATAS = [
    (os.path.join(FONTS, "ZTi-Han"), "字体输出/ZTi-Han"),
    (os.path.join(FONTS, "ZTi-Sym"), "字体输出/ZTi-Sym"),
    (os.path.join(APP, "fonts", "fontcal.json"), "fonts"),
    (os.path.join(APP, "fonts", "fontcal_glyph.json"), "fonts"),
    (os.path.join(APP, "fonts", "LongCang-Regular-merged.ttf"), "fonts"),
    (os.path.join(APP, "fonts", "MaShanZheng-Regular-merged.ttf"), "fonts"),
    (os.path.join(APP, "fonts", "ZhiMangXing-Regular-merged.ttf"), "fonts"),
    (os.path.join(APP, "vendor"), "vendor"),
    (os.path.join(APP, "assets"), "assets"),
]

HIDDEN = [
    "mthw", "mthw.math_renderer", "mthw.handwriting", "mthw.config",
    "mthw.latex_parser",
]

a = Analysis([os.path.join(APP, "做题窗口.py")], pathex=[APP],
             datas=DATAS, hiddenimports=HIDDEN, noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, a.binaries, a.datas, [],
          name="ZTi智能做题助手", icon=ICON_PATH, debug=False, strip=False, upx=False,
          console=False, onefile=True)
