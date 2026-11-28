# -*- mode: python ; coding: utf-8 -*-
"""打包自检 spec（**不随产品发布**）。

用与 `ZTi.spec` 完全相同的 excludes / TOC 裁剪规则，把 tests/pack_smoke.py
打成一个**控制台** exe：不需要授权、不弹窗，跑完写 pack_smoke.json 并返回码。

目的：证明「瘦身后留下来的依赖集」还能 import、还能跑出与开发环境
逐像素一致的渲染结果。正式包因为要看授权界面，没法在无人值守下自检。

用法（在 源码/pdf-layer 下）：
    $env:PYTHONPATH = "<项目>\\归档\\旧版本\\_work\\pylibs"
    py -3 -m PyInstaller --noconfirm --clean `
        --distpath "..\\..\\发布\\_build\\smoke_dist" `
        --workpath "..\\..\\发布\\_build\\smoke_build" `
        "packaging\\ZTi-smoke.spec"

注意：字体用**明文** 字体输出/（正式包才加密），否则没有授权解不开。
"""
import os
import sys as _sys

if SPECPATH not in _sys.path:
    _sys.path.insert(0, SPECPATH)
import build_common as _bc  # noqa: E402

APP = _bc.APP
FONTS = _bc.FONTS

FIXTURE = os.path.join(APP, "tests", "fixtures", "worksheet", "作业A.pdf")

DATAS = [
    (FONTS, "字体输出"),                      # 明文字体（自检用）
    (os.path.join(APP, "fonts", "fontcal.json"), "fonts"),
    (os.path.join(APP, "fonts", "fontcal_glyph.json"), "fonts"),
    (os.path.join(APP, "vendor"), "vendor"),
    (os.path.join(APP, "assets"), "assets"),
    (FIXTURE, "."),                            # → _internal/pack_fixture.pdf
]

a_main = Analysis([os.path.join(APP, "tests", "pack_smoke.py")],
                  pathex=[APP], datas=DATAS, hiddenimports=_bc.HIDDEN,
                  excludes=_bc.EXCLUDES, noarchive=False)
_bc.slim_analysis(a_main)

pyz_main = PYZ(a_main.pure)
exe_main = EXE(pyz_main, a_main.scripts, [],
               exclude_binaries=True, name="ZTi-pack-smoke",
               debug=False, strip=False, upx=False, console=True)

coll = COLLECT(exe_main, a_main.binaries, a_main.datas,
               strip=False, upx=False, name="ZTi-pack-smoke")
