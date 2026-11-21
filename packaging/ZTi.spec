# -*- mode: python ; coding: utf-8 -*-
"""ZTi 智能做题助手打包 spec：

主程序 = 做题窗口（做题软件）；手写体生成器作为附带的「手写工具」；
三个 exe 共享一份运行时（_internal）。
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

# 主程序：做题窗口（做题软件）
a_main = Analysis([os.path.join(APP, "做题窗口.py")], pathex=[APP],
                  datas=DATAS, hiddenimports=HIDDEN, noarchive=False)
pyz_main = PYZ(a_main.pure)
exe_main = EXE(pyz_main, a_main.scripts, [],
               exclude_binaries=True, name="ZTi智能做题助手", icon=ICON_PATH,
               debug=False, strip=False, upx=False, console=False)

# 附带工具：手写体生成器（GUI）
a_gui = Analysis([os.path.join(APP, "gui.py")], pathex=[APP],
                 datas=[], hiddenimports=HIDDEN, noarchive=False)
pyz_gui = PYZ(a_gui.pure)
exe_gui = EXE(pyz_gui, a_gui.scripts, [],
              exclude_binaries=True, name="ZTi手写工具", icon=ICON_PATH,
              debug=False, strip=False, upx=False, console=False)

# AI 接口（给 AI/自动化调用，可不开）
a_api = Analysis([os.path.join(APP, "ai_api.py")], pathex=[APP],
                 datas=[], hiddenimports=HIDDEN, noarchive=False)
pyz_api = PYZ(a_api.pure)
exe_api = EXE(pyz_api, a_api.scripts, [],
              exclude_binaries=True, name="ZTiAI接口", icon=ICON_PATH,
              debug=False, strip=False, upx=False, console=True)

coll = COLLECT(exe_main, a_main.binaries, a_main.datas,
               exe_gui, a_gui.binaries,
               exe_api, a_api.binaries,
               strip=False, upx=False, name="ZTi智能做题助手")
