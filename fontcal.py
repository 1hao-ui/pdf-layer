# -*- coding: utf-8 -*-
"""fontcal.py — 字库的字号对齐（只对齐大小，不校准观感）。

为什么只做字号
--------------
早先这里还做「墨色增益 / 伽马 / 形态学加粗」，那是因为当时符号来自
Segoe UI Symbol（印刷体），和毛笔体汉字在墨量、笔画粗细上天然不同。

现在符号和汉字都出自**同一个人手写的同一套字库**（ZTi-Sym 4 套 + 后续的
汉字库），笔宽、墨色、清晰度本来就是一致的，再校准反而会破坏原笔迹：

  · 加粗（MaxFilter 膨胀）会把笔锋糊掉，丢掉手写的起收笔
  · alpha 增益会改变墨色深浅，同一支笔的字深浅不一反而更假

所以现在只保留一件事：**把不同字库的「字高」统一到同一标准**。
ZTi-Sym 的符号实测只有汉字字高的 0.57–0.86 倍，不放大就会明显偏小。

    cal = {"size_scale": 1.22, "dy": 0.0, "dx": 0.25}

对外接口保持不变（`params_for` / `finish_tile` / `coverage` /
`style_compatible`），只是 `finish_tile` 不再改像素，只调偏移。
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CAL_PATH = os.path.join(HERE, "fonts", "fontcal.json")
GLYPH_CAL_PATH = os.path.join(HERE, "fonts", "fontcal_glyph.json")
# 手写体生成器项目根（pdf-layer 上两级）：字体输出/ 下的新手写字库在这里。
PROJECT_ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

REF_PX = 54
SAMPLE = "相电压功率流生电的"          # CJK 样本
SAMPLE_ASCII = "0123456789"            # 数字样本（只含符号的字库用）

# 数字/符号的目标高度 = 汉字高度 × 该比例。
# 中文手写里阿拉伯数字通常比汉字略小，0.86 是目视协调的值。
ASCII_HEIGHT_RATIO = 0.86

# 只渲染 ASCII/符号的字体：必须用 ASCII 样本测，且不做垂直位移补偿
# （数字要严格坐在基线上）。
ASCII_FONTS = (
    "LongCang-Regular-merged.ttf",
    "pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf",
    "ZTi-Sym-推荐-pass2.ttf",
)

DEFAULTS = {
    "size_scale": 1.0,      # 字号缩放（乘到渲染字号上）
    "dy": 0.0,              # 垂直位移（px，正=下移）
    "dx": 0.0,              # 水平位移（px）
    "adv_scale": 1.0,       # 前进宽度缩放：只影响字距，不改字形大小
}


def cal_key(path):
    """校准表的键：项目内的字体用「项目相对路径」，否则用 basename。

    为什么不能一律用 basename：ZTi-Han 和 ZTi-Sym 都有 pass1.ttf / pass2.ttf，
    同名但字高完全不同（640 vs 500 units），用 basename 会互相串校准值，
    汉字会被按符号的倍率放大。
    """
    if not path:
        return ""
    p = os.path.normpath(os.path.abspath(path))
    try:
        rel = os.path.relpath(p, PROJECT_ROOT)
        if not rel.startswith(".."):
            return rel.replace(os.sep, "/")
    except Exception:
        pass
    return os.path.basename(p)


def _lookup_keys(path):
    """查表时要依次尝试的键：仓库布局 fonts/… → 项目相对路径 → basename。"""
    keys = []
    p = os.path.normpath(os.path.abspath(path)).replace(os.sep, "/")
    i = p.find("/fonts/")
    if i >= 0:
        keys.append(p[i + 1:])          # 仓库内克隆：fonts/ZTi-*/passN.ttf
    k = cal_key(path)
    if k:
        keys.append(k)
    b = os.path.basename(path)
    if b and b not in keys:
        keys.append(b)
    return keys


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


def _measure(path, sample, size_scale=1.0):
    """渲染样本，测字高 / 墨色 / 笔画 / 基线偏移。"""
    import statistics

    from PIL import Image, ImageDraw, ImageFont

    alphas, runs, heights, dys, dxs = [], [], [], [], []
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

    def med(x):
        return statistics.median(x) if x else 0.0

    return {"alpha": med(alphas), "stroke": med(runs), "height": med(heights),
            "dy": med(dys), "dx": med(dxs)}


def calibrate(fonts, base_font, save=True, rounds=3):
    """以 base_font 为基准，算出每套字体的**字号缩放**（闭环迭代到字高一致）。

    只算 size_scale / dy / dx，不算墨色与加粗——见模块开头的说明。
    """
    base = _measure(base_font, SAMPLE)
    table = {}
    for path, sample, h_ratio in _specs(fonts):
        if not os.path.exists(path):
            continue
        key = cal_key(path)
        target_h = base["height"] * h_ratio
        if os.path.abspath(path) == os.path.abspath(base_font):
            table[key] = dict(DEFAULTS)
            continue
        c = dict(DEFAULTS)
        m = _measure(path, sample)
        if m["height"] > 0:
            c["size_scale"] = target_h / m["height"]

        # 闭环：放大后抗锯齿像素变多、字高会略变，迭代到稳定
        for _ in range(rounds + 2):
            mm = _measure(path, sample, c["size_scale"])
            if not mm or mm["height"] <= 0:
                break
            c["size_scale"] *= target_h / mm["height"]

        # 位置：ASCII 字库不做垂直补偿（数字严格坐基线）；
        # 其他字库按墨迹中心偏差补，让基线与主字体一致。
        if sample is SAMPLE_ASCII or os.path.basename(path) in ASCII_FONTS:
            c["dy"] = 0.0
        else:
            c["dy"] = round(base["dy"] - m["dy"], 3)
        c["dx"] = round(base["dx"] - m["dx"], 3)
        c["size_scale"] = round(c["size_scale"], 4)
        table[key] = c

    if save:
        try:
            os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
            with open(CAL_PATH, "w", encoding="utf-8") as fp:
                json.dump({"ref_px": REF_PX, "sample": SAMPLE,
                           "ascii_sample": SAMPLE_ASCII,
                           "ascii_height_ratio": ASCII_HEIGHT_RATIO,
                           "base": os.path.basename(base_font),
                           "note": "只做字号对齐；墨色/粗细/清晰度不校准",
                           "fonts": table}, fp, ensure_ascii=False, indent=2)
        except Exception:
            pass
    return table


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


_glyph_cache = None


def load_glyph_table():
    """读逐字校准表 {字体名: {字符: 缩放}}；没有则返回空。"""
    global _glyph_cache
    if _glyph_cache is not None:
        return _glyph_cache
    try:
        with open(GLYPH_CAL_PATH, encoding="utf-8") as fp:
            _glyph_cache = json.load(fp).get("fonts", {})
    except Exception:
        _glyph_cache = {}
    return _glyph_cache


def glyph_scale(path, ch):
    """取某字体下某个字符的逐字缩放系数；没有则 1.0。

    注意：系数是在参考字号下测的，实际渲染字号不同时会有偏差
    （抗锯齿让墨迹高度非线性）。调用方若需要精确对齐，
    应使用 glyph_scale_at(path, ch, px, target_h)。
    """
    if not path:
        return 1.0
    tab = load_glyph_table().get(os.path.basename(path))
    if not tab:
        return 1.0
    return float(tab.get(ch, 1.0))


_ink_h_cache = {}


def ink_height_at(path, ch, px):
    """在指定字号下量字符的墨迹高度（px）。"""
    key = (os.path.basename(path), ch, int(px))
    if key in _ink_h_cache:
        return _ink_h_cache[key]
    try:
        from PIL import Image, ImageDraw, ImageFont
        f = ImageFont.truetype(path, max(4, int(px)))
        bb = f.getbbox(ch)
        if bb is None:
            return None
        w = max(1, bb[2] - bb[0]) + 16
        h = max(1, bb[3] - bb[1]) + 16
        im = Image.new("L", (w, h), 0)
        ImageDraw.Draw(im).text((8 - bb[0], 8 - bb[1]), ch, fill=255, font=f)
        bb2 = im.getbbox()
        val = (bb2[3] - bb2[1]) if bb2 else None
    except Exception:
        val = None
    _ink_h_cache[key] = val
    return val


def glyph_scale_at(path, ch, px, target_h):
    """在实际字号下闭环修正缩放，使墨迹高度逼近 target_h。

    参考字号下算出的系数搬到别的字号会偏（抗锯齿非线性），
    这里直接在当前字号上迭代两次，保证真正对齐。
    """
    base = glyph_scale(path, ch)
    if not target_h or target_h <= 0:
        return base
    k = base
    for _ in range(2):
        h = ink_height_at(path, ch, max(4, px * k))
        if not h:
            break
        k *= target_h / h
    return max(0.5, min(2.0, k))


def params_for(path):
    """取某套字体的校准参数（缺失则用默认值 = 不校准）。

    先按「项目相对路径」查（区分 ZTi-Han/pass2 与 ZTi-Sym/pass2），
    查不到再按 basename 查（兼容旧表）。
    """
    if not path:
        return dict(DEFAULTS)
    tab = load()
    got = None
    for k in _lookup_keys(path):
        if k in tab:
            got = tab[k]
            break
    if not got:
        return dict(DEFAULTS)
    p = dict(DEFAULTS)
    p.update({k: v for k, v in got.items() if k in DEFAULTS})
    return p


def finish_tile(img, ox, oy, cal):
    """贴图收尾：**只调偏移，不动像素**。

    早先这里会做膨胀加粗 + alpha 增益，但那会破坏原笔迹（笔锋被糊掉、
    墨色被改动）。现在符号与汉字同源，观感天然一致，不需要再补偿。
    """
    if img is None:
        return None, ox, oy
    lim = max(2.0, img.width * 0.35)
    dx = max(-lim, min(lim, cal.get("dx", 0.0)))
    return img, ox + dx, oy + cal.get("dy", 0.0)


def coverage(paths, text, need_ratio=0.5):
    """正文里有多少比例的 CJK 字符能被「足够多套」字库覆盖。"""
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


def style_fingerprint(path, size=120, chars="相电压功率流生电的"):
    """量一套字库的「书写风格指纹」（笔画宽 / 墨迹占比）。"""
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
    return {"stroke": wmed, "ink": statistics.mean(inks) if inks else 0}


def style_compatible(path_a, path_b, tol=0.35):
    """两套字库是否属于同一书写风格（笔画宽 / 墨迹占比都在容差内）。

    用于挡住「毛笔体 vs 硬笔体」这类明显不同源的混排。
    同一人写的多遍（如 ZTi-Sym 的 4 套）能过。
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
    # 人手写符号字库：仓库内 fonts/ZTi-Sym/ 优先，其次项目外的「字体输出/ZTi-Sym」
    for sym in (os.environ.get("ZTISYM_DIR", ""),
                os.path.join(repo, "fonts", "ZTi-Sym"),
                os.path.normpath(os.path.join(repo, "..", "..", "字体输出", "ZTi-Sym"))):
        if sym and os.path.isdir(sym):
            fonts += [os.path.join(sym, n) for n in sorted(os.listdir(sym))
                      if n.endswith((".otf", ".ttf")) and not n.startswith("ZTi-Sym-推荐")]
            break
    if os.path.exists(base):
        fonts = [base] + [p for p in fonts if os.path.abspath(p) != os.path.abspath(base)]

    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        for path, sample, ratio in _specs(fonts):
            if os.path.exists(path):
                print("%-34s %s" % (os.path.basename(path), _measure(path, sample)))
        sys.exit(0)

    table = calibrate(fonts, base)
    print("%-34s %10s %8s %8s" % ("字体", "字号倍", "dy", "dx"))
    for k, v in table.items():
        print("%-34s %10.3f %+8.2f %+8.2f"
              % (k, v["size_scale"], v["dy"], v["dx"]))
    print("\n已写入", CAL_PATH)
