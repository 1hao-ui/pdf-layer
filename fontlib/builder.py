# -*- coding: utf-8 -*-
"""builder.py — 归一化字块 → 多套 glyf TTF。

一个字的最终形状由三层决定（都能在界面上看到/改）：

    scan 里写的大小与位置
      × 库缩放 scale（自动按中位字高算，可拖）
      × 该字微调 scale / dx / dy（个别字歪了才动）
      → 贴到 1000 units 的 em 里：水平居中到 500，墨迹底边落在 baseline

矢量化和现有 ZTi-Han/ZTi-Sym 一致：按「行条带的矩形」直接写 glyf，
不依赖 potrace/opencv，孔洞（口/日）靠非零环绕自然保留。
"""
from __future__ import annotations

import datetime
import json
import os
import statistics

import numpy as np
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

UPM = 1000
ASCENT, DESCENT = 800, -200
CELL_NORM = 240


def gname(cp):
    return "uni%04X" % cp if cp <= 0xFFFF else "u%05X" % cp


# ---------------------------------------------------------------- 矢量
def mask_to_glyph(mask, scale, baseline, dx=0.0, dy=0.0,
                  max_ink=980.0):
    """掩膜 → (glyph, 墨迹像素数)。

    mask     CELL_NORM×CELL_NORM 的 bool 掩膜（原点左上）
    scale    缩放：1 个掩膜像素 = scale 个 font units
    baseline 墨迹底边的 font units 坐标
    dx/dy    该字微调（font units）
    """
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return TTGlyphPen(None).glyph(), 0
    ink_w = (xs.max() - xs.min() + 1) * scale
    ink_h = (ys.max() - ys.min() + 1) * scale
    # 防止个别写大了的字撑出 em：按墨迹长边收一下
    fit = min(1.0, max_ink / ink_w if ink_w else 1.0,
              max_ink / ink_h if ink_h else 1.0)
    if fit < 1.0:
        scale *= fit
        ink_w = (xs.max() - xs.min() + 1) * scale
    x_off = (UPM / 2.0 + dx) - (xs.min() + (xs.max() - xs.min() + 1) / 2.0) * scale
    y_top = baseline + dy + (ys.max() + 1) * scale

    pen = TTGlyphPen(None)
    active = {}                       # (x0, x1) -> [r0, r1]

    def emit(key, r0, r1):
        x0, x1 = key
        X0, X1 = x_off + x0 * scale, x_off + x1 * scale
        Y0, Y1 = y_top - r1 * scale, y_top - r0 * scale
        pen.moveTo((X0, Y0))
        pen.lineTo((X1, Y0))
        pen.lineTo((X1, Y1))
        pen.lineTo((X0, Y1))
        pen.closePath()

    for r in range(int(ys.min()), int(ys.max()) + 1):
        row = mask[r]
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        runs = set(zip(starts.tolist(), ends.tolist()))
        for key in list(active):
            if key not in runs or active[key][1] != r - 1:
                emit(key, active[key][0], active[key][1] + 1)
                del active[key]
        for key in runs:
            if key in active:
                active[key][1] = r
            else:
                active[key] = [r, r]
    for key, (r0, r1) in active.items():
        emit(key, r0, r1 + 1)
    return pen.glyph(), int(len(xs))


def _build_ttf(path, char_to_mask, family, ps_name, params, tweaks,
               created=""):
    """组装一份标准 glyf TTF；返回 {字符: 墨迹像素}。"""
    scale_base = float(params.get("scale", 1.0)) * (UPM / float(CELL_NORM))
    baseline = float(params.get("baseline", 100))
    cmap, glyphs, ink_stats = {}, {}, {}
    for ch, mask in sorted(char_to_mask.items(), key=lambda kv: ord(kv[0])):
        tw = (tweaks or {}).get(ch) or {}
        s = scale_base * float(tw.get("scale", 1.0))
        g, ink = mask_to_glyph(mask, s, baseline,
                               dx=float(tw.get("dx", 0.0)),
                               dy=float(tw.get("dy", 0.0)))
        n = gname(ord(ch))
        glyphs[n] = g
        cmap[ord(ch)] = n
        ink_stats[ch] = ink

    order = [".notdef"] + [gname(ord(c)) for c in sorted(char_to_mask)]
    glyph_set = {".notdef": TTGlyphPen(None).glyph()}
    glyph_set.update(glyphs)

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyph_set)
    fb.setupHorizontalMetrics({g: (UPM, 0) for g in order})
    fb.setupHorizontalHeader(ascent=ASCENT, descent=DESCENT, lineGap=0)
    fb.setupNameTable({
        1: family, 2: "Regular", 3: "%s %s" % (family, ps_name),
        4: "%s %s" % (family, ps_name),
        5: "Version 1.0（%s）" % (created or _today()),
        6: ps_name,
        8: "自建字库工作台",
        9: "作者亲笔",
        10: "作者亲笔手写，保留所有权利；仅限本机本项目使用，不得再分发。",
        11: "自建字库工作台（fontlib）",
        13: "Proprietary. Personal handwriting; all rights reserved.",
    })
    fb.setupOS2(sTypoAscender=ASCENT, sTypoDescender=DESCENT,
                usWinAscent=ASCENT, usWinDescent=-DESCENT, usWeightClass=400)
    fb.setupPost()
    fb.setupMaxp()
    fb.save(path)
    return ink_stats


def _today():
    return datetime.date.today().isoformat()


# ---------------------------------------------------------------- 选样本
def group_samples(cells):
    """{字符: [(pass, page, mask, meta)]}，同字不同遍攒在一起。"""
    by = {}
    for ch, mask, meta in cells:
        if meta.get("blank"):
            continue
        by.setdefault(ch, []).append(
            (meta.get("pass", 1), meta.get("page", 0), mask, meta))
    for ch in by:
        by[ch].sort(key=lambda t: (t[0], t[1]))
    return by


def import_fonts(font_paths, params, max_glyphs=30000):
    """把现有字库的字形「搬进」新库（补字/合并用）。

    做法：按新库的缩放 ``params['scale']`` 反推字号，把每个字渲染成
    240×240 掩膜；建库时再乘回同一个 scale，墨迹高正好落在 700 units，
    与手写新字同一把尺子。位置不重要——建库时统一按墨迹外框居中/贴基线。
    """
    from PIL import Image, ImageDraw, ImageFont
    try:
        from . import charsets
        visible = charsets.is_visible
    except Exception:
        def visible(ch):
            return bool(ch and not ch.isspace())

    s = float(params.get("scale") or 1.0)
    s = max(0.5, min(2.5, s))
    px = int(round(CELL_NORM / s))
    px = max(48, min(420, px))
    out = {}
    total = 0
    for p in font_paths or []:
        try:
            from fontTools.ttLib import TTFont
            cm = TTFont(p).getBestCmap() or {}
            font = ImageFont.truetype(p, px)
        except Exception:
            continue
        for cp in sorted(cm):
            if total >= max_glyphs:
                break
            ch = chr(cp)
            if not visible(ch):
                continue
            tile = Image.new("L", (CELL_NORM, CELL_NORM), 0)
            try:
                ImageDraw.Draw(tile).text(
                    (CELL_NORM / 2.0, CELL_NORM / 2.0), ch, font=font,
                    fill=255, anchor="mm")
            except Exception:
                continue
            m = np.asarray(tile, dtype=np.uint8) > 96
            if not m.any():
                continue
            out.setdefault(ch, []).append(m)
            total += 1
    return out


def choose(by_char, passes):
    """每遍挑一个样本：第 k 遍用该字第 k 个样本，不够就跨遍复用。"""
    out = []
    for k in range(1, passes + 1):
        chosen = {}
        for ch, samples in by_char.items():
            chosen[ch] = samples[(k - 1) % len(samples)][2]
        out.append(chosen)
    return out


# ---------------------------------------------------------------- 主流程
def build_library(cells, out_dir, lib_id, name, passes=2, params=None,
                  tweaks=None, family=None, progress=None,
                  write_preview=True):
    """把切好的字块建成本地字库。

    返回
    ----
    {"id","dir","passes":[路径],"chars":[...],"missing":[...],
     "calibration":{路径: {...}},"stats":{...}}
    """
    os.makedirs(out_dir, exist_ok=True)
    params = dict(params or {})
    params.setdefault("scale", 1.0)
    params.setdefault("baseline", 100)
    params.setdefault("dx", 0.0)
    params.setdefault("dy", 0.0)
    tweaks = dict(tweaks or {})
    family = family or name or lib_id
    by_char = group_samples(cells)
    all_chars = set(c for c, _, _ in cells)
    have = set(by_char)
    chosen_passes = choose(by_char, max(1, int(passes)))

    pass_paths, report = [], {"passes": [], "chars": {}, "created": _today(),
                              "params": params, "missing": sorted(all_chars - have)}
    for k, chosen in enumerate(chosen_passes, 1):
        p = os.path.join(out_dir, "pass%d.ttf" % k)
        ink = _build_ttf(p, chosen, "%s%d" % (family, k),
                         ("%s%d" % (family, k)).replace(" ", ""),
                         params, tweaks)
        pass_paths.append(p)
        report["passes"].append({"pass": k, "file": os.path.basename(p),
                                 "chars": len(chosen),
                                 "ink_min": min(ink.values()) if ink else 0})
        if progress:
            progress(k, len(chosen_passes), "第 %d 遍" % k)

    for ch, samples in sorted(by_char.items(), key=lambda kv: ord(kv[0])):
        report["chars"][ch] = {"samples": len(samples),
                               "forms": min(len(samples), passes),
                               "meta": [{kk: vv for kk, vv in s[3].items()
                                         if kk in ("h", "w", "bottom", "cx",
                                                   "edge", "ink")}
                                        for s in samples]}

    # 校准值：建完立即算，渲染侧马上能用
    cal = {}
    try:
        from . import autotune
        cal = autotune.calibration_for(pass_paths)
    except Exception as e:
        report["calibration_error"] = repr(e)

    meta = {
        "id": lib_id,
        "name": name,
        "kind": "han",
        "dir": os.path.basename(out_dir),
        "passes": [os.path.basename(p) for p in pass_paths],
        "char_count": len(have),
        "chars": sorted(have, key=ord),
        "params": params,
        "tweaks": tweaks,
        "calibration": {os.path.basename(p): v
                        for p, v in cal.items()},
        "created": _today(),
        "license": "author",
        "source": "self-built",
        "stats": {"cells": len(cells), "usable": len(have),
                  "missing": len(all_chars - have)},
    }
    with open(os.path.join(out_dir, "字库.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "build_report.json"), "w",
              encoding="utf-8") as fp:
        json.dump(report, fp, ensure_ascii=False, indent=2)

    preview_path = ""
    if write_preview:
        try:
            preview_path = glyph_sheet(pass_paths[0], out_dir)
        except Exception:
            preview_path = ""

    meta["dir"] = out_dir
    meta["passes_abs"] = pass_paths
    meta["calibration"] = cal
    meta["report"] = report
    meta["preview"] = preview_path
    return meta


def glyph_sheet(font_path, out_dir, chars=None, cols=16, cell=64,
                size_pt=44):
    """字库字形总览（每格一个字，核对有没有歪/缺）。"""
    from PIL import Image, ImageDraw, ImageFont
    try:
        from fontTools.ttLib import TTFont
        cm = TTFont(font_path).getBestCmap() or {}
    except Exception:
        return ""
    pool = [chr(cp) for cp in sorted(cm)]
    if chars:
        pool = [c for c in chars if c in pool]
    if not pool:
        return ""
    rows = int(np.ceil(len(pool) / float(cols)))
    img = Image.new("RGB", (cols * cell, rows * (cell + 12)), "white")
    d = ImageDraw.Draw(img)
    try:
        f = ImageFont.truetype(font_path, size_pt)
    except Exception:
        return ""
    for i, ch in enumerate(pool):
        c, r = i % cols, i // cols
        x, y = c * cell, r * (cell + 12)
        d.rectangle((x, y, x + cell - 1, y + cell - 1),
                    outline=(232, 232, 232))
        try:
            bb = d.textbbox((0, 0), ch, font=f)
            d.text((x + (cell - (bb[2] - bb[0])) / 2 - bb[0],
                    y + (cell - (bb[3] - bb[1])) / 2 - bb[1]),
                   ch, font=f, fill=(20, 20, 22))
        except Exception:
            continue
    p = os.path.join(out_dir, "预览_字形.png")
    img.save(p)
    return p
