# -*- mode: python ; coding: utf-8 -*-
"""ZTi 手写体生成器打包 spec：三个 exe 共享一份运行时（_internal）。"""
import os

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

a_gui = Analysis([os.path.join(APP, "gui.py")], pathex=[APP],
                 datas=DATAS, hiddenimports=HIDDEN, noarchive=False)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [],
              exclude_binaries=True, name="ZTi手写体生成器",
              debug=False, strip=False, upx=False, console=False)

a_ws = Analysis([os.path.join(APP, "做题窗口.py")], pathex=[APP],
                datas=[], hiddenimports=HIDDEN, noarchive=False)
pyz_ws = PYZ(a_ws.pure)
exe_ws = EXE(pyz_ws, a_ws.scripts, [],
             exclude_binaries=True, name="ZTi做题窗口",
             debug=False, strip=False, upx=False, console=False)

a_api = Analysis([os.path.join(APP, "ai_api.py")], pathex=[APP],
                 datas=[], hiddenimports=HIDDEN, noarchive=False)
pyz_api = PYZ(a_api.pure)
exe_api = EXE(pyz_api, a_api.scripts, [],
              exclude_binaries=True, name="ZTiAI接口",
              debug=False, strip=False, upx=False, console=True)

coll = COLLECT(exe_gui, a_gui.binaries, a_gui.datas,
               exe_ws, a_ws.binaries,
               exe_api, a_api.binaries,
               strip=False, upx=False, name="ZTi手写体生成器")
