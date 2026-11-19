# -*- coding: utf-8 -*-
"""fontcal.py — 多套字库的观感归一化（墨色 / 粗细 / 字号 / 清晰度 / 中心位置）。

为什么需要
----------
多套真人笔迹字库（ZTi 系列）来自字嗨导出与手稿扫描，与主字体（马善政）在
五个维度上天然不一致。实测同一参考字号下：

    字体        墨色   笔画   字高   垂直偏移  水平偏移
    马善政      202    6.0    46     -23.0     0.0
    ZTi-01      144    2.0    29     -23.5     +0.5
    ZTi-Scan-01 148    3.0    33     -21.5    -10.5

另外，正文里的数字/拉丁走龙藏体（硬笔），实测字高只有汉字的 57%——
不校准的话「380V」「17.4kW」里的数字会明显偏小，中英文数字看起来不是一套字。

本模块离线测量一次、闭环迭代出每套字体的校准系数，缓存成 fonts/fontcal.json：

  · 字号：按实测墨迹高缩放到目标高度（CJK 对齐汉字；数字对齐汉字 ×0.86）
  · 粗细：笔画细的用形态学膨胀补（比描边自然；伽马只能改墨色，改不了笔画宽度）
  · 墨色：alpha 增益 + 伽马，阻尼迭代后做一次精确修正
  · 中心位置：dy/dx 补偿墨迹中心偏移（手稿切字常偏左 10+px）
  · 清晰度：只测不校（实测差异 <6%，强行锐化会出现振铃）
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CAL_PATH = os.path.join(HERE, "fonts", "fontcal.json")

REF_PX = 54
SAMPLE = "相电压功率流生电的"          # CJK 样本
SAMPLE_ASCII = "0123456789"            # 数字样本（拉丁/数字专用字体）
ASCII_HEIGHT_RATIO = 0.86              # 数字目标高度 = 汉字 × 0.86
ASCII_FONTS = ("LongCang-Regular-merged.ttf",)

DEFAULTS = {
    "size_scale": 1.0,
    "alpha_gain": 1.0,
    "alpha_gamma": 1.0,
    "thicken": 0.0,
    "dy": 0.0,
    "dx": 0.0,
}


def _specs(fonts):
    """把字体列表规范成 (路径, 样本, 目标高度比例)。"""
    out = []
    for item in fonts:
        if isinstance(item, tuple):
            out.append(item)
        else:
            is_ascii = os.path.basename(item) in ASCII_FONTS
            out.append((item,
                        SAMPLE_ASCII if is_ascii else SAMPLE,
                        ASCII_HEIGHT_RATIO if is_ascii else 1.0))
    return out


def _measure(path, sample, size_scale=1.0, cal=None):
    """渲染样本并测五项指标；size_scale/cal 用于闭环验证。"""
    import statistics

    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    cal = cal or DEFAULTS
    alphas, runs, heights, dys, dxs, sharps = [], [], [], [], [], []
    for ch in sample:
        try:
            px = max(4, int(round(REF_PX * size_scale)))
            f = ImageFont.truetype(path, px)
            bbox = f.getbbox(ch)
        except Exception:
            continue
        if bbox is None:
            continue
        pad = 8
        w = max(1, bbox[2] - bbox[0]) + 2 * pad
        h = max(1, bbox[3] - bbox[1]) + 2 * pad
        img = Image.new("L", (w, h), 0)
        ImageDraw.Draw(img).text((pad - bbox[0], pad - bbox[1]), ch, fill=255, font=f)

        # 笔画加粗（与渲染路径一致）
        th = cal.get("thicken", 0.0)
        if th >= 0.5:
            k = min(9, max(3, int(round(th)) * 2 + 1))
            img = img.filter(ImageFilter.MaxFilter(k))
        # 墨色增益 + 伽马
        g, gm = cal.get("alpha_gain", 1.0), cal.get("alpha_gamma", 1.0)
        if abs(g - 1.0) > 1e-6 or abs(gm - 1.0) > 1e-6:
            lut = [max(0, min(255, int(round(255.0 * ((i / 255.0) ** gm) * g))))
                   for i in range(256)]
            img = img.point(lut)

        vals = [v for v in img.getdata() if v > 0]
        if not vals:
            continue
        alphas.append(statistics.mean(vals))

        rr = []
        for y in range(h):
            run = 0
            for x in range(w):
                if img.getpixel((x, y)) > 40:
                    run += 1
                elif run:
                    rr.append(run)
                    run = 0
            if run:
                rr.append(run)
        if rr:
            runs.append(statistics.median(rr))

        bb = img.getbbox()
        if bb:
            heights.append(bb[3] - bb[1])
            baseline = pad - bbox[1] + bbox[3]
            dys.append(((bb[1] + bb[3]) / 2.0) - baseline)
            face_cx = pad + (bbox[2] - bbox[0]) / 2.0
            dxs.append(((bb[0] + bb[2]) / 2.0) - face_cx)

        lap = img.filter(ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                            scale=1, offset=0))
        lv = [v for v in lap.getdata() if v > 0]
        if lv:
            sharps.append(statistics.pvariance(lv) ** 0.5)

    def med(x):
        return statistics.median(x) if x else 0.0

    return {"alpha": med(alphas), "stroke": med(runs), "height": med(heights),
            "sharp": med(sharps), "dy": med(dys), "dx": med(dxs)}


def calibrate(fonts, base_font, save=True, rounds=3):
    """以 base_font 为基准，闭环迭代算出每套字体的校准系数。"""
    base = _measure(base_font, SAMPLE)
    table = {}
    for path, sample, h_ratio in _specs(fonts):
        if not os.path.exists(path):
            continue
        key = os.path.basename(path)
        target_h = base["height"] * h_ratio
        if os.path.abspath(path) == os.path.abspath(base_font):
            table[key] = dict(DEFAULTS)
            continue
        c = dict(DEFAULTS)
        m = _measure(path, sample)
        if m["height"] > 0:
            c["size_scale"] = target_h / m["height"]
        if m["alpha"] > 0:
            c["alpha_gain"] = min(2.2, base["alpha"] / m["alpha"])
        if m["stroke"] > 0:
            gap = base["stroke"] - m["stroke"]
            c["thicken"] = max(0.0, min(3.0, gap / 2.0))
            ratio = base["stroke"] / max(0.1, m["stroke"] + 2 * c["thicken"])
            c["alpha_gamma"] = max(0.6, min(1.0, 1.0 / (ratio ** 0.3)))
        c["dy"] = base["dy"] - m["dy"]
        c["dx"] = base["dx"] - m["dx"]

        # 闭环：只迭代字号/墨色/笔画（三者互相影响）。位置必须在固定字号下测，
        # 否则字号一变基线跟着变，误差会累积放大（实测 dy 会涨到 40+ px）。
        for _ in range(rounds + 2):
            mm = _measure(path, sample, c["size_scale"], c)
            if not mm or mm["height"] <= 0:
                break
            c["size_scale"] *= target_h / mm["height"]
            if mm["alpha"] > 0:
                err_a = base["alpha"] / mm["alpha"]
                c["alpha_gain"] = min(2.6, c["alpha_gain"] * (1.0 + (err_a - 1.0) * 0.5))
            if mm["stroke"] > 0 and base["stroke"] > 0:
                gap = base["stroke"] - mm["stroke"]
                c["thicken"] = max(0.0, min(3.0, c["thicken"] + gap / 2.0 * 0.6))

        # 收尾：阻尼迭代会留残差，用实测值做一次精确的墨色修正
        for _ in range(2):
            mm = _measure(path, sample, c["size_scale"], c)
            if mm and mm["alpha"] > 0 and base["alpha"] > 0:
                c["alpha_gain"] = min(2.6, max(0.3,
                                            c["alpha_gain"] * base["alpha"] / mm["alpha"]))
            else:
                break

        for k in ("size_scale", "alpha_gain", "alpha_gamma", "thicken"):
            c[k] = round(c[k], 4)
        for k in ("dy", "dx"):
            c[k] = round(c[k], 3)
        table[key] = c
    if save:
        try:
            os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
            with open(CAL_PATH, "w", encoding="utf-8") as fp:
                json.dump({"ref_px": REF_PX, "sample": SAMPLE,
                           "ascii_sample": SAMPLE_ASCII,
                           "ascii_height_ratio": ASCII_HEIGHT_RATIO,
                           "base": os.path.basename(base_font),
                           "fonts": table}, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return table


def style_fingerprint(path, size=120, chars="相电压功率流生电的"):
    """量一套字库的「书写风格指纹」。

    校准只能统一色号/粗细/字号，**改不了书写工具**：
    毛笔体的笔画有明显粗细起伏（变异系数大、墨迹占比高），
    硬笔体是等宽线（变异系数小、墨迹占比低）。实测马善政 vs ZTi：

        指标       马善政   ZTi-01  ZTi-Scan-01
        笔画宽     11.0     4.0     5.0        (比值 0.36 / 0.45)
        墨迹占比   0.20     0.058   0.081      (比值 0.29 / 0.41)

    差到这种程度，放大+膨胀只会得到「变粗的硬笔线」，风格仍然割裂。
    所以混排前必须过这道指纹检查。
    """
    import statistics

    import numpy as np
    from PIL import Image, ImageDraw, ImageFont

    widths, inks = [], []
    for ch in chars:
        try:
            f = ImageFont.truetype(path, size)
            bb = f.getbbox(ch)
        except Exception:
            continue
        if bb is None:
            continue
        pad = 10
        w = max(1, bb[2] - bb[0]) + 2 * pad
        h = max(1, bb[3] - bb[1]) + 2 * pad
        im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(im).text((pad - bb[0], pad - bb[1]), ch, fill=255, font=f)
        a = np.asarray(im)
        mask = a > 60
        if not mask.any():
            continue
        inks.append(float(mask.mean()))
        for row in mask:
            run = 0
            for v in row:
                if v:
                    run += 1
                elif run:
                    widths.append(run)
                    run = 0
            if run:
                widths.append(run)
    wmed = statistics.median(widths) if widths else 0
    return {
        "stroke": wmed,
        "ink": statistics.mean(inks) if inks else 0,
    }


def style_compatible(path_a, path_b, tol=0.35):
    """两套字库是否属于同一书写风格（笔画宽 / 墨迹占比都在容差内）。

    返回 (是否兼容, 说明)。容差 0.35 意味着允许 ±35% 的差异——
    同一支笔不同人写、或同一人不同速写都能过；毛笔 vs 硬笔过不了。
    """
    try:
        fa = style_fingerprint(path_a)
        fb = style_fingerprint(path_b)
    except Exception as e:
        return False, "指纹测量失败: %r" % (e,)
    if not fa["stroke"] or not fb["stroke"]:
        return False, "无法测量笔画宽度"
    r_stroke = fb["stroke"] / fa["stroke"]
    r_ink = (fb["ink"] / fa["ink"]) if fa["ink"] else 0
    bad = []
    if abs(r_stroke - 1) > tol:
        bad.append("笔画宽 %.2fx" % r_stroke)
    if abs(r_ink - 1) > tol:
        bad.append("墨迹占比 %.2fx" % r_ink)
    if bad:
        return False, "风格不符（%s）" % "，".join(bad)
    return True, "ok（笔画宽 %.2fx，墨迹 %.2fx）" % (r_stroke, r_ink)


_cache = None


def load():
    """读缓存的校准表；没有就返回空表（等价于不校准）。"""
    global _cache
    if _cache is not None:
        return _cache
    try:
        with open(CAL_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
        _cache = data.get("fonts", {})
    except Exception:
        _cache = {}
    return _cache


def params_for(path):
    """取某套字体的校准参数（缺失则用默认值 = 不校准）。"""
    if not path:
        return dict(DEFAULTS)
    got = load().get(os.path.basename(path))
    if not got:
        return dict(DEFAULTS)
    p = dict(DEFAULTS)
    p.update(got)
    return p


def apply_alpha(img, gain, gamma):
    """墨色增益 + 伽马（保形，不改形状与抗锯齿）。"""
    if abs(gain - 1.0) < 1e-6 and abs(gamma - 1.0) < 1e-6:
        return img
    lut = []
    for i in range(256):
        v = 255.0 * ((i / 255.0) ** gamma) * gain
        lut.append(max(0, min(255, int(round(v)))))
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lut))
    return out


def apply_thicken(img, px):
    """笔画加粗 px 个像素（形态学膨胀）。

    为什么需要：ZTi 系列笔画只有 2–3px，主字体是 6px。放大字号不会让笔画变粗
    （只是整体变大），伽马也只改墨色深浅——真正让笔画变粗必须做形态学膨胀。
    """
    if px < 0.5:
        return img
    from PIL import ImageFilter
    k = min(9, max(3, int(round(px)) * 2 + 1))
    out = img.copy()
    out.putalpha(img.getchannel("A").filter(ImageFilter.MaxFilter(k)))
    return out


def finish_tile(img, ox, oy, cal):
    """贴图收尾：笔画加粗 + 墨色增益 + 中心位置补偿。

    位移通过修改 ox/oy 实现——不改变贴图尺寸，也不会把墨迹裁掉。
    dx 的语义是「字形墨迹中心相对字面中心的偏差」，改 ox 只让字形在字框内移动，
    advance 不变（保证行宽不漂移）。
    """
    if img is None:
        return None, ox, oy
    out = apply_thicken(img, cal.get("thicken", 0.0))
    out = apply_alpha(out, cal["alpha_gain"], cal["alpha_gamma"])
    lim = max(2.0, img.width * 0.35)
    dx = max(-lim, min(lim, cal["dx"]))
    return out, ox + dx, oy + cal["dy"]


def coverage(paths, text, need_ratio=0.5):
    """正文里有多少比例的 CJK 字符能被「足够多套」字库覆盖。

    只要求「任意一套有」是不够的：若 8 套里只有 1 套有这个字，其余 7 次
    出现都会回退到主字体（毛笔体），混排照样割裂。所以按
    「至少 need_ratio 比例的套有该字」才算这个字合格。
    """
    try:
        from fontTools.ttLib import TTFont
    except Exception:
        return 1.0
    cmaps = []
    for p in paths:
        try:
            cmaps.append(set(TTFont(p).getBestCmap() or {}))
        except Exception:
            continue
    if not cmaps:
        return 0.0
    need = max(1, int(round(len(cmaps) * need_ratio)))
    chars = [c for c in text if not c.isspace() and not c.isascii()]
    if not chars:
        return 1.0
    hit = sum(1 for c in chars if sum(1 for cm in cmaps if ord(c) in cm) >= need)
    return hit / float(len(chars))


if __name__ == "__main__":
    import sys
    repo = HERE
    base = os.path.join(repo, "fonts", "MaShanZheng-Regular-merged.ttf")
    fonts = []
    for name in ("MaShanZheng-Regular-merged.ttf", "LongCang-Regular-merged.ttf",
                 "ZhiMangXing-Regular-merged.ttf", "LiuJianMaoCao-Regular-merged.ttf"):
        p = os.path.join(repo, "fonts", name)
        if os.path.exists(p):
            fonts.append(p)
    zti = os.path.join(repo, "fonts", "ZTi")
    if os.path.isdir(zti):
        fonts += [os.path.join(zti, n) for n in sorted(os.listdir(zti))
                  if n.endswith((".otf", ".ttf"))]
    if os.path.exists(base):
        fonts = [base] + [p for p in fonts if os.path.abspath(p) != os.path.abspath(base)]

    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        for path, sample, ratio in _specs(fonts):
            if os.path.exists(path):
                print("%-34s %s" % (os.path.basename(path),
                                    _measure(path, sample)))
        sys.exit(0)

    table = calibrate(fonts, base)
    print("%-34s %8s %8s %8s %8s %8s %8s"
          % ("字体", "字号倍", "墨色倍", "伽马", "加粗", "dy", "dx"))
    for k, v in table.items():
        print("%-34s %8.3f %8.3f %8.3f %8.2f %+8.2f %+8.2f"
              % (k, v["size_scale"], v["alpha_gain"], v["alpha_gamma"],
                 v["thicken"], v["dy"], v["dx"]))
    print("\n已写入", CAL_PATH)
