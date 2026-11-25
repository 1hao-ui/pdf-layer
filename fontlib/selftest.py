# -*- coding: utf-8 -*-
"""selftest.py — 无头跑通「选字 → 模板 → 合成扫描 → 识别 → 建库 → 校准」。

用作者自己的字库（ZTi-Han / ZTi-Sym）在内存里「写」出扫描件，不依赖打印机
和人工标注，所以可以放进自动化测试。它验证的是链路，不是观感：

* 模板分页/角标/网格坐标和 intake 是否对得上（差 1pt 就会切到邻字）
* 自动阈值能不能把浅灰参考字滤掉、只留深墨
* 透视/旋转校正后字块是否仍居中
* 建库后的 TTF cmap 是否覆盖全部字、校准值是否落在合理区间
"""
from __future__ import annotations

import json
import os
import random
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import builder, intake, template


def _font_has(font_path, ch):
    try:
        from fontTools.ttLib import TTFont
        cm = TTFont(font_path).getBestCmap() or {}
        return ord(ch) in cm
    except Exception:
        return False


def _pick_writer(writing_fonts, ch):
    """给合成页挑一个「有这个字」的字体；都没有就退到系统楷体/雅黑。

    只用于**合成测试数据**（模拟用户手写），不是渲染链路。
    真实流程里字是用户手写的，不依赖任何现成字体。
    """
    for p in writing_fonts:
        if p and os.path.exists(p) and _font_has(p, ch):
            return p
    for p in ("C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/msyh.ttc"):
        if os.path.exists(p) and _font_has(p, ch):
            return p
    return writing_fonts[0] if writing_fonts else ""


def pick_test_chars(n_han=24, n_sym=12):
    """挑一批现有字库里真有的字（汉字 + 符号）。"""
    from . import charsets
    han_font = _font("han")
    sym_font = _font("sym")
    han = [c for c in charsets.font_chars(han_font) if charsets.is_cjk(c)]
    sym = [c for c in "0123456789+-=×÷≈<>" if c in charsets.font_chars(sym_font)]
    rnd = random.Random(7)
    han = rnd.sample(sorted(han), min(n_han, len(han)))
    sym = sym[:n_sym]
    return han + sym


def _font(kind):
    try:
        from . import registry
        p = registry.active_font(kind)
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    base = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "字体输出"))
    p = os.path.join(base, "ZTi-Han" if kind == "han" else "ZTi-Sym",
                     "pass2.ttf")
    return p if os.path.exists(p) else ""


def synth_page(layout, page_index, writing_fonts, dpi=200, rotate=0.0,
               ink=(28, 28, 32)):
    """按 layout 画一张「写满字的扫描页」。"""
    page = layout["pages"][page_index]
    chars = page.get("chars") or []
    g = layout["grid"]
    k = dpi / 72.0
    W, H = int(round(595 * k)), int(round(842 * k))
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    # 四角黑块
    m, mk = 22.0, 14.0
    for (x, y) in ((m, m), (595 - m - mk, m), (m, 842 - m - mk),
                   (595 - m - mk, 842 - m - mk)):
        d.rectangle((x * k, y * k, (x + mk) * k, (y + mk) * k), fill=0)
    # 格线（浅灰）
    for i, ch in enumerate(chars):
        col, row = i % g["cols"], i // g["cols"]
        x0 = (g["left"] + col * g["cell_w"]) * k
        y0 = (g["top"] + row * g["cell_h"]) * k
        x1 = x0 + g["cell_w"] * k
        y1 = y0 + g["cell_h"] * k
        d.rectangle((x0, y0, x1, y1), outline=(208, 208, 208), width=1)
        # 参考字：很浅，阈值应滤掉（按字所属字库选，避免缺字）
        try:
            rf_path = _pick_writer(writing_fonts, ch)
            rf = ImageFont.truetype(rf_path, max(8, int((y1 - y0) * 0.52)))
            d.text(((x0 + x1) / 2, (y0 + y1) / 2), ch, font=rf,
                   fill=(224, 224, 224), anchor="mm")
        except Exception:
            pass
        # 「手写」深墨：每格单独渲染后贴回，避免溢出到邻格
        cw, chh = int(x1 - x0), int(y1 - y0)
        tile = Image.new("L", (cw, chh), 255)
        td = ImageDraw.Draw(tile)
        # 汉字用汉字库、符号用符号库，保证每页都写满全部字
        fp = _pick_writer(writing_fonts, ch)
        fs = max(10, int(min(cw, chh) * 0.62))
        try:
            f = ImageFont.truetype(fp, fs)
        except Exception:
            continue
        bb = td.textbbox((0, 0), ch, font=f)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        jx = random.Random(page_index * 10000 + i).randint(-2, 2)
        jy = random.Random(page_index * 10000 + i + 1).randint(-2, 2)
        td.text(((cw - tw) / 2 - bb[0] + jx, (chh - th) / 2 - bb[1] + jy),
                ch, font=f, fill=20)
        mask = Image.new("L", (cw, chh), 0)
        mask.paste(tile, (0, 0))
        dark = Image.new("RGB", (cw, chh), ink)
        img.paste(dark, (int(x0), int(y0)),
                  mask.point(lambda v: 255 if v < 128 else 0))
    if rotate:
        img = img.rotate(rotate, resample=Image.Resampling.BICUBIC,
                         fillcolor=(255, 255, 255), expand=False)
    return img


def run(work_dir=None, chars=None, passes=2, cell_pt=32.0, dpi=200,
        keep=False):
    """完整链路自检；返回报告 dict（失败会抛异常）。"""
    tmp = work_dir or tempfile.mkdtemp(prefix="fontlab_selftest_")
    chars = chars or pick_test_chars()
    old_root = os.environ.get("ZTIFONT_ROOT")
    font_root = os.path.join(tmp, "字体输出")
    os.makedirs(font_root, exist_ok=True)
    os.environ["ZTIFONT_ROOT"] = font_root
    try:
        from . import registry
        registry.ensure(font_root)
        from . import session
        s = session.Session.create(os.path.join(tmp, "proj"), "selftest-lib",
                                   "自检字库", chars, passes=passes,
                                   cell_pt=cell_pt)
        files = s.make_template()
        layout = template.load_layout(files["layout"])
        fonts = [p for p in (_font("han"), _font("sym")) if p]
        images = []
        for pi in range(len(layout["pages"])):
            img = synth_page(layout, pi, fonts, dpi=dpi,
                             rotate=(0 if pi == 0 else 1.5))
            p = os.path.join(tmp, "scan%02d.jpg" % pi)
            img.save(p, quality=92)
            images.append(p)
        s.add_images(images)
        summary = s.analyze(threshold="auto", auto_align=True)
        sug = s.suggest()
        meta = s.build(activate=False, write_calibration=False)
        # 校验
        from fontTools.ttLib import TTFont
        cm = set(TTFont(meta["passes_abs"][0]).getBestCmap() or {})
        missing = [c for c in chars if ord(c) not in cm]
        sizes = [v.get("size_scale") for v in meta["calibration"].values()]
        report = {
            "ok": not missing and bool(sizes) and all(0.6 < x < 2.2 for x in sizes),
            "work_dir": tmp,
            "chars": len(chars),
            "built": meta["char_count"],
            "missing": "".join(missing),
            "pages": len(layout["pages"]),
            "cells": summary["cells"],
            "blank": summary["blank"],
            "edge": summary["edge"],
            "scale": sug["params"]["scale"],
            "size_scale": sizes,
            "warnings": summary["warnings"][:5],
        }
        return report
    finally:
        if old_root is None:
            os.environ.pop("ZTIFONT_ROOT", None)
        else:
            os.environ["ZTIFONT_ROOT"] = old_root
        if not keep and work_dir is None:
            pass    # 保留临时目录，失败时可查；调用方决定是否删


if __name__ == "__main__":
    import sys
    rep = run(work_dir=(sys.argv[1] if len(sys.argv) > 1 else None))
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sys.exit(0 if rep["ok"] else 1)
