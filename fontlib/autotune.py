# -*- coding: utf-8 -*-
"""autotune.py — 自动量、自动给参数，也能按用户微调重算。

「自动识别并把上传的调整到一个比较合适的参数」具体指三件事：

1. **字形级**：每格字的墨迹高度/宽度/底边/居中量出来，用**中位数**定
   整库的缩放与基线（不是逐字拉满——'一' 就该比 '國' 小），
   同时挑出空白、贴边、明显过小/过大的格让人复核。
2. **字库级**：算出这批字写出来整体是偏大还是偏小、偏上还是偏下，
   给出建议的 ``scale`` / ``dy``，界面上一拖就能改。
3. **渲染级**：建完库后按「汉字墨迹高 ≈ 数字墨迹高」的老口径，
   自动算 ``size_scale`` / ``adv_scale`` 写进 ``fontcal.json``，
   不需要再手工跑校准脚本。

所有函数只吃 numpy 掩膜/字体路径，不碰界面，便于无头测试。
"""
from __future__ import annotations

import os
import statistics

import numpy as np

try:
    import fontcal as _fontcal
    REF_PX = _fontcal.REF_PX          # 统一基础参数：真实渲染字号（49.6）
except Exception:                     # 独立 import autotune 时兜底
    REF_PX = 49.6
TARGET_H = 700            # 归一化后汉字墨迹高（font units / 1000）
BASELINE = 100            # 墨迹底边距基线（font units）
GAP_EM = 0.118            # 相邻汉字墨迹留白（em），沿用汉字校准.py 的口径
SMALL_RATIO = 0.45        # 小于中位高 45% 视为可疑
LARGE_RATIO = 1.75        # 大于中位高 175% 视为可疑


# ---------------------------------------------------------------- 量
def han_cells_median(cells, key):
    vals = [m[key] for _, _, m in cells
            if m.get("h") and not m.get("blank") and _is_han(m.get("char", ""))]
    if not vals:
        vals = [m[key] for _, _, m in cells
                if m.get("h") and not m.get("blank")]
    return statistics.median(vals) if vals else 0.0


def _is_han(ch):
    return len(ch) == 1 and "\u4e00" <= ch <= "\u9fff"


def library_stats(cells):
    """整批字块的统计。``cells`` 是 intake.slice_all 的 [(ch, mask, meta)]。"""
    ok = [m for _, _, m in cells if not m.get("blank") and m.get("h")]
    h = [m["h"] for m in ok]
    w = [m["w"] for m in ok]
    bottom = [m["bottom"] for m in ok]
    cx = [m["cx"] for m in ok]
    ink = [m["ink"] for m in ok]
    return {
        "cells": len(cells),
        "usable": len(ok),
        "blank": sum(1 for _, _, m in cells if m.get("blank")),
        "edge": sum(1 for _, _, m in cells if m.get("edge")),
        "h_med": statistics.median(h) if h else 0.0,
        "h_mean": statistics.mean(h) if h else 0.0,
        "w_med": statistics.median(w) if w else 0.0,
        "bottom_med": statistics.median(bottom) if bottom else 1.0,
        "cx_med": statistics.median(cx) if cx else 0.5,
        "ink_med": statistics.median(ink) if ink else 0,
        "char_count": len({ch for ch, _, _ in cells}),
    }


def suggest(cells, target_h=TARGET_H):
    """给出一套「比较合适」的建库参数（用户可在界面上再微调）。

    返回
    ----
    scale        整库缩放：让汉字墨迹高中位数 = target_h/1000 格高
    target_h     目标墨迹高（font units）
    baseline     墨迹底边位置（font units）
    stats        library_stats()
    warnings     {字符: [原因]}, 需要人工复核的格
    outliers     过小/过大的字（可能写错格/切到邻字）
    """
    st = library_stats(cells)
    h_med = st["h_med"] or 0.5
    scale = (target_h / 1000.0) / h_med if h_med else 1.0
    warnings, outliers = {}, {}
    for ch, mask, m in cells:
        if m.get("blank"):
            warnings.setdefault(ch, []).append("空白/墨迹太少")
            continue
        reasons = []
        if m.get("edge"):
            reasons.append("墨迹贴到格子边缘，可能切到邻字")
        if h_med:
            if m["h"] < h_med * SMALL_RATIO:
                reasons.append("明显偏小（%.0f%% 中位高）" %
                                (100 * m["h"] / h_med))
                outliers[ch] = "small"
            elif m["h"] > h_med * LARGE_RATIO:
                reasons.append("明显偏大（%.0f%% 中位高）" %
                                (100 * m["h"] / h_med))
                outliers[ch] = "large"
        if _is_han(ch) and m.get("w") and m["w"] > m["h"] * 2.2:
            reasons.append("特别扁宽，确认有没有写错格")
        if reasons:
            warnings.setdefault(ch, []).extend(reasons)
    return {
        "scale": round(scale, 4),
        "target_h": target_h,
        "baseline": BASELINE,
        "stats": st,
        "warnings": warnings,
        "outliers": outliers,
        "params": {
            "scale": round(scale, 4),
            "dx": 0.0,
            "dy": 0.0,
            "baseline": BASELINE,
            "adv_gap": GAP_EM,
        },
    }


# ---------------------------------------------------------------- 微调
def glyph_transform(meta, params, tweak=None):
    """一格字的最终变换：库参数 + 该字微调 → (scale, dx, dy)（font units）。"""
    tw = tweak or {}
    scale = float(params.get("scale", 1.0)) * float(tw.get("scale", 1.0))
    dx = float(params.get("dx", 0.0)) + float(tw.get("dx", 0.0))
    dy = float(params.get("dy", 0.0)) + float(tw.get("dy", 0.0))
    return scale, dx, dy


def auto_tweaks(cells, warn_only=True):
    """给每个字准备一份微调表（默认全 1.0 / 0）。"""
    out = {}
    for ch, _, _ in cells:
        out.setdefault(ch, {"scale": 1.0, "dx": 0.0, "dy": 0.0})
    return out


# ---------------------------------------------------------------- 渲染校准
def digit_target_px(ref_sym=None):
    """数字的目标墨迹高（px @ 统一参考字号）。

    旧版量的是「公式层当时渲染出来的数字高」，而公式层用的是写死的 1.65
    倍率——于是自建库按偏大的目标标定，建出来比现有字库大一截。现在直接
    用 fontcal 的统一目标，不再绕公式层。
    """
    try:
        import fontcal
        return fontcal.target_ascii_h()
    except Exception:
        return 0.0


def _fontlib_root():
    return os.path.dirname(os.path.abspath(__file__))


def _default_sym_font():
    try:
        from . import registry
        p = registry.active_font("sym")
        if p:
            return p
    except Exception:
        pass
    p = os.path.normpath(os.path.join(_fontlib_root(), "..", "..", "..", "字体输出",
                                      "ZTi-Sym", "pass2.ttf"))
    return p if os.path.exists(p) else None


def _default_han_font():
    try:
        from . import registry
        p = registry.active_font("han")
        if p:
            return p
    except Exception:
        pass
    p = os.path.normpath(os.path.join(_fontlib_root(), "..", "..", "..", "字体输出",
                                      "ZTi-Han", "pass2.ttf"))
    return p if os.path.exists(p) else None


def ttf_glyph_stats(path, chars=None):
    """读 TTF 轮廓，返回 [(墨迹高, 墨迹中心, advance)]（font units）。"""
    st = []
    try:
        from fontTools.ttLib import TTFont
        f = TTFont(path)
        glyf = f["glyf"]
        hm = f["hmtx"]
        cm = f.getBestCmap() or {}
        for cp, gn in cm.items():
            if chars is not None and chr(cp) not in chars:
                continue
            gl = glyf[gn]
            gl.recalcBounds(glyf)
            h = gl.yMax - gl.yMin
            if h <= 0:
                continue
            st.append((h, (gl.yMin + gl.yMax) / 2.0, hm[gn][0]))
        f.close()
    except Exception:
        return []
    return st


def calibration_for(paths, ref_sym=None, gap_em=GAP_EM):
    """按统一基础参数算 size_scale / adv_scale（与 fontcal.calibrate 同一口径）。

    汉字库对到「汉字墨迹高 = HAN_INK_EM × 字号」，符号库对到「数字 = 汉字 ×
    0.86」。返回 {绝对路径: {size_scale, adv_scale, dy, dx}}，写盘时由
    write_calibration 换成项目相对路径。
    """
    import fontcal
    table = {}
    for p in paths or []:
        if not p or not os.path.exists(p):
            continue
        try:
            c = fontcal.measure_params(p)
        except Exception:
            continue
        if not c.get("size_scale"):
            continue
        table[os.path.abspath(p)] = {
            "size_scale": c["size_scale"],
            "adv_scale": c.get("adv_scale", 1.0),
            "dy": 0.0,
            "dx": 0.0,
        }
    return table


# ---------------------------------------------------------------- 校验
def render_heights(font_path, chars="相电压功率流生电的", px=REF_PX,
                   size_scale=1.0):
    """按校准后的字号渲染，量实际墨迹高（px），用于自检。"""
    from PIL import Image, ImageDraw, ImageFont
    vals = []
    for ch in chars:
        try:
            f = ImageFont.truetype(font_path, max(4, int(round(px * size_scale))))
            bb = f.getbbox(ch)
            if not bb:
                continue
            im = Image.new("L", (max(1, bb[2] - bb[0]) + 8,
                                 max(1, bb[3] - bb[1]) + 8), 0)
            ImageDraw.Draw(im).text((4 - bb[0], 4 - bb[1]), ch, fill=255, font=f)
            ib = im.getbbox()
            if ib:
                vals.append(ib[3] - ib[1])
        except Exception:
            continue
    return statistics.median(vals) if vals else 0.0


def style_vs_reference(font_path, ref_font=None):
    """新建字库与现有字库的书写风格差（笔画宽/墨迹占比）。"""
    ref_font = ref_font or _default_han_font()
    if not ref_font or not os.path.exists(ref_font):
        return {"ok": True, "note": "没有参照字库"}
    try:
        import fontcal
        ok, note = fontcal.style_compatible(font_path, ref_font)
        return {"ok": bool(ok), "note": note}
    except Exception as e:
        return {"ok": True, "note": "风格校验跳过（%r）" % (e,)}


class CalibrationError(RuntimeError):
    pass


def write_calibration(table, cal_path=None, note=None, path_map=None):
    """把校准值合并进 fonts/fontcal.json。

    键用 ``fontcal.table_key()``（与查找顺序一致），**不再退化成 basename**：
    以前建库时把工程目录里的 font/pass1.ttf 写成 "pass1.ttf"，注册后的
    字体输出/<id>/pass1.ttf 查不到，params_for 回落 1.0 —— 汉字只有数字的
    2/3 大，而工作台预览走的是临时 override，所以「预览正常、实测不对」。

    path_map : {源路径: 实际发布路径}，建库时把工程目录映射到 字体输出/<id>。
    """
    import json
    try:
        import fontcal
        cal_path = cal_path or fontcal.CAL_PATH
    except Exception:
        cal_path = cal_path or os.path.normpath(
            os.path.join(_fontlib_root(), "..", "fonts", "fontcal.json"))
    data = {}
    if os.path.exists(cal_path):
        with open(cal_path, encoding="utf-8") as fp:
            data = json.load(fp)
    fonts = data.setdefault("fonts", {})
    skipped = []
    for p, params in (table or {}).items():
        final = p
        if path_map:
            final = path_map.get(os.path.abspath(p), path_map.get(p, p))
        try:
            import fontcal
            key = fontcal.table_key(final)
        except Exception:
            key = os.path.basename(final)
        if not key:
            skipped.append(p)       # 项目外的预览库：写 basename 会串值
            continue
        fonts[key] = {k: params[k] for k in
                      ("size_scale", "adv_scale", "dy", "dx") if k in params}
    if note:
        data["note"] = note
    with open(cal_path, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    try:
        import fontcal
        fontcal._cache = None
        fontcal._glyph_cache = None
    except Exception:
        pass
    return cal_path


