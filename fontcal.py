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

所以现在只保留一件事：**把不同字库的「字高」统一到同一标准**，而且这个
标准是**绝对目标**（见下面的 BASE_SIZE_PT / HAN_INK_EM），不依赖任何一款
基准字体——旧基准马善政已于 2026-11-25 移出字库，再按它标定会让新写的
字库整体偏大或偏小（同一段里汉字和数字差 20%~50% 就是这么来的）。

    cal = {"size_scale": 1.49, "adv_scale": 0.72, "dy": 0.0, "dx": 0.0}

对外接口保持不变（`params_for` / `finish_tile` / `coverage` /
`style_compatible`），只是 `finish_tile` 不再改像素，只调偏移。
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 打包成 exe 后：数据在 sys._MEIPASS，fontcal.json 在 _MEIPASS/fonts，
# 字库在 _MEIPASS/字体输出；此时 PROJECT_ROOT 取 _MEIPASS，
# 这样 rel 键正好是「字体输出/ZTi-Han/pass1.ttf」，与校准表一致。
_FROZEN = bool(getattr(sys, "frozen", False))
_BASE = os.path.normpath(getattr(sys, "_MEIPASS", HERE)) if _FROZEN else HERE
CAL_PATH = os.path.join(_BASE, "fonts", "fontcal.json")
GLYPH_CAL_PATH = os.path.join(_BASE, "fonts", "fontcal_glyph.json")
# 手写体生成器项目根（pdf-layer 上两级）：字体输出/ 下的新手写字库在这里。
PROJECT_ROOT = _BASE if _FROZEN else os.path.normpath(
    os.path.join(HERE, "..", ".."))

# ---- 基础参数（唯一口径：所有字库、所有层都对到这几个数上）----
# 以前这些数散在三处且各按各的基准标定：fontcal.REF_PX=54、
# hwrender.DEFAULT_SIZE_PT=12.4、formula.MATH_SIZE_COMP=1.65。基准字体
# （马善政）2026-11-25 移出字库后，旧表的相对关系就失效了：同一段里
# 汉字、数字、公式各差 10%~50%。现在统一成：
#     汉字墨迹高 = HAN_INK_EM × 渲染字号
#     数字/拉丁 = 汉字墨迹高 × ASCII_HEIGHT_RATIO
# 并且校准就在真实渲染字号 REF_PX 上做（54 是旧字号，比实际大 9%）。
BASE_SIZE_PT = 12.4             # 与 hwrender.DEFAULT_SIZE_PT 一致
SCALE = 4                       # 与 hwrender.SCALE 一致（1pt = 4px）
REF_PX = BASE_SIZE_PT * SCALE   # 49.6：校准/量墨迹都在这个字号上
HAN_INK_EM = 0.948              # 正文汉字墨迹高 = 渲染字号 × 0.948（≈满格）
ASCII_HEIGHT_RATIO = 0.86       # 中文手写里数字通常比汉字略小
ADV_GAP_EM = 0.118              # 相邻汉字墨迹留白（em），决定 adv_scale

SAMPLE = "相电压功率流生电的"          # CJK 样本
SAMPLE_ASCII = "0123456789"            # 数字样本（只含符号的字库用）


def target_han_h(px=None):
    """正文汉字的目标墨迹高（px）；px 默认取真实渲染字号 REF_PX。"""
    return HAN_INK_EM * float(REF_PX if px is None else px)


def target_ascii_h(px=None):
    """数字/拉丁/符号的目标墨迹高（px）。"""
    return target_han_h(px) * ASCII_HEIGHT_RATIO

# 只渲染 ASCII/符号的字体：必须用 ASCII 样本测，且不做垂直位移补偿
# （数字要严格坐在基线上）。这张表只用于「读不到 cmap 时」的兜底判断，
# 现在优先按 cmap 里有没有汉字来判断，避免 ZTi-Han 与 ZTi-Sym 同名 passN 串味。
ASCII_FONTS = (
    "pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf",
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


def table_key(path):
    """**写表**时用的键：与 `_lookup_keys` 的查找顺序保持一致。

    · 仓库内的 fonts/… → 写成 `fonts/ZTi-01.otf`（历史键，查找时最先命中）
    · 项目内的 字体输出/… → 写成 `字体输出/ZTi-Han/pass2.ttf`
    · 项目外的字体（临时目录里的预览库）→ 返回空串，调用方跳过不写：
      这种路径只能退化成 basename，会和正式字库的 passN.ttf 串值。
    """
    if not path:
        return ""
    p = os.path.normpath(os.path.abspath(path)).replace(os.sep, "/")
    i = p.find("/fonts/")
    if i >= 0:
        return p[i + 1:]
    k = cal_key(path)
    return "" if "/" not in k else k


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


def _looks_ascii(path):
    """字体里没有汉字 → 按符号字库处理（用 ASCII 样本、不做垂直补偿）。

    比只看文件名可靠：ZTi-Han 和 ZTi-Sym 都有 pass2.ttf，按 basename 会把
    汉字库当符号库标定。
    """
    try:
        from fontTools.ttLib import TTFont
        cm = TTFont(path).getBestCmap() or {}
        return not any(0x4E00 <= cp <= 0x9FFF for cp in cm)
    except Exception:
        return os.path.basename(path) in ASCII_FONTS


_cmap_cache = {}


def _cmap(path):
    """字体 cmap（读不到返回 None）。"""
    key = os.path.abspath(path)
    if key not in _cmap_cache:
        try:
            from fontTools.ttLib import TTFont
            _cmap_cache[key] = set(TTFont(path).getBestCmap() or {})
        except Exception:
            _cmap_cache[key] = None
    return _cmap_cache[key]


def _sample_for(path):
    """这套字体该用哪些样本字量高度。

    自建字库往往只有一两百个字，未必包含默认的 9 字样本；缺样本会导致
    量不到字高、size_scale 回落 1.0（汉字只有数字的 2/3 大）。所以样本
    先用「参考样本 ∩ 字库」，不够就用字库自己的字。
    """
    cm = _cmap(path)
    if cm is None:
        return SAMPLE
    if not any(0x4E00 <= cp <= 0x9FFF for cp in cm):
        got = "".join(c for c in SAMPLE_ASCII if ord(c) in cm)
        return got or SAMPLE_ASCII
    got = "".join(c for c in SAMPLE if ord(c) in cm)
    if len(got) >= 5:
        return got
    own = [chr(cp) for cp in sorted(cm) if 0x4E00 <= cp <= 0x9FFF]
    return "".join(own[:12]) or SAMPLE


def _glyph_em(path):
    """字体汉字墨迹高的 em 中位数（font units / upm），用来算字距。"""
    import statistics

    try:
        from fontTools.pens.boundsPen import BoundsPen
        from fontTools.ttLib import TTFont
        f = TTFont(path)
        upm = float(f["head"].unitsPerEm) or 1000.0
        gs = f.getGlyphSet()
        vals = []
        for cp, name in (f.getBestCmap() or {}).items():
            ch = chr(cp)
            if not (0x4E00 <= cp <= 0x9FFF) and ch not in SAMPLE_ASCII:
                continue
            bp = BoundsPen(gs)
            try:
                gs[name].draw(bp)
            except Exception:
                continue
            if bp.bounds:
                vals.append((bp.bounds[3] - bp.bounds[1]) / upm)
        f.close()
        return statistics.median(vals) if vals else 0.0
    except Exception:
        return 0.0


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


def _fit_scale(path, sample, target_h, rounds=3):
    """闭环求 size_scale：让样本墨迹高（中位）= target_h。

    为什么要闭环：放大后抗锯齿像素数会变，字高并不严格按字号线性增长。
    返回 (scale, 最后一次测量结果)；量不到字高时返回 (None, m)。
    """
    m = _measure(path, sample, 1.0)
    if not m["height"] or not target_h:
        return None, m
    scale = target_h / m["height"]
    for _ in range(rounds + 2):
        mm = _measure(path, sample, scale)
        if not mm["height"]:
            break
        k = target_h / mm["height"]
        m = mm
        if abs(k - 1.0) <= 0.01:
            break
        scale *= k
    return scale, m


def measure_params(path, sample=None, target_h=None, rounds=3):
    """单套字体的校准值（不读表、不写表），返回 DEFAULTS 形状的 dict。

    汉字库 → 汉字墨迹高 = target_han_h()；符号库 → 数字墨迹高 = target_ascii_h()。
    这是「同一段里所有字符一样大」的唯一算法：所有字库（内置/自建/老 ZTi）
    都走这里，不再各自按某个基准字体标定。
    """
    is_ascii = _looks_ascii(path)
    sample = sample or _sample_for(path)
    target = float(target_h or (target_ascii_h() if is_ascii else target_han_h()))
    scale, _m = _fit_scale(path, sample, target, rounds=rounds)
    c = dict(DEFAULTS)
    if not scale:
        return c
    c["size_scale"] = round(scale, 4)
    if not is_ascii:
        # 字距 = 墨迹高 + 固定留白。墨迹 em 用**刚测出来的样本**（target/scale/REF_PX），
        # 不用全字表 em：自建库里个别大字会把 em 拉高，不同字库的留白就会忽紧忽松。
        ink_em = target / (c["size_scale"] * REF_PX)
        if ink_em > 0:
            c["adv_scale"] = round(ink_em + ADV_GAP_EM / c["size_scale"], 4)
    return c


def _active_han_font():
    """注册表里启用的汉字库主字体；没有就退到项目里的 ZTi-Han。"""
    try:
        from fontlib import registry
        p = registry.active_font("han")
        if p and os.path.exists(p):
            return p
    except Exception:
        pass
    for p in (os.path.join(PROJECT_ROOT, "字体输出", "ZTi-Han", "pass2.ttf"),
              os.path.join(HERE, "fonts", "ZTi-Han", "pass2.ttf")):
        if os.path.exists(p):
            return p
    return None


def known_fonts():
    """当前所有会被渲染用到的字体：注册表里的字库 + 老 ZTi 回退库。"""
    out = []
    try:
        from fontlib import registry
        for lib in registry.libraries():
            out.extend(registry.pass_paths(lib))
    except Exception:
        pass
    zti = os.path.join(HERE, "fonts", "ZTi")
    if os.path.isdir(zti):
        out += [os.path.join(zti, n) for n in sorted(os.listdir(zti))
                if n.lower().endswith((".ttf", ".otf"))]
    seen, uniq = set(), []
    for p in out:
        a = os.path.abspath(p)
        if a not in seen and os.path.exists(p):
            seen.add(a)
            uniq.append(p)
    return uniq


def calibrate(fonts, base_font=None, save=True, rounds=3, note=None):
    """把所有字体对齐到同一套基础参数，返回 {键: 参数}。

    · 汉字库：size_scale 让汉字墨迹高 = target_han_h()
    · 符号库：size_scale 让数字墨迹高 = target_ascii_h()
    · adv_scale：汉字库按「墨迹高 + ADV_GAP_EM」重算；符号库不动
    · dy/dx：手上调过的沿用旧值，其余按基准字体的墨迹中心补

    base_font 只用来对齐 dy/dx（默认取注册表启用的汉字库）。字号是
    **绝对目标**，不再依赖某个基准字体——旧基准马善政已移出字库，
    再按它标定会让新字库整体偏大/偏小。
    """
    old = load()
    base_font = base_font or _active_han_font()
    paths = [p for p in (fonts or []) if p and os.path.exists(p)]
    if base_font and os.path.abspath(base_font) not in [
            os.path.abspath(p) for p in paths] and os.path.exists(base_font):
        paths.insert(0, base_font)
    if not paths:
        return {}
    if not base_font:
        base_font = paths[0]

    base_prev = old.get(cal_key(base_font)) or {}
    base_cal = dict(DEFAULTS)
    base_cal.update(base_prev)
    if not base_prev:
        base_cal = measure_params(base_font)
    base_m = _measure(base_font, _sample_for(base_font),
                      base_cal.get("size_scale", 1.0) or 1.0)

    table = {}
    for path in paths:
        is_ascii = _looks_ascii(path)
        c = measure_params(path, rounds=rounds)
        key = table_key(path)
        if not key:
            # 项目外的字体（预览/临时库）：写 basename 会和正式字库串值，
            # 交给 params_for 的自动兜底即可，不落盘。
            continue
        prev = old.get(key) or old.get(os.path.basename(path)) or {}
        if os.path.abspath(path) == os.path.abspath(base_font):
            c["dy"] = 0.0
            c["dx"] = float(prev.get("dx", 0.0))
        else:
            m = _measure(path, _sample_for(path), c["size_scale"])
            if is_ascii:
                c["dy"] = 0.0          # 数字要严格坐在基线上
            else:
                # _measure 的 dy 混进了字体的下伸空白（不同字体差别可达十几 px），
                # 不能当基线偏移用：渲染时 hwrender 已按 ascent 对齐基线。
                # 这里只保留旧表里小幅手调值（±3px），其余归零。
                prev_dy = float(prev["dy"]) if "dy" in prev else 0.0
                c["dy"] = round(prev_dy if abs(prev_dy) <= 3.0 else 0.0, 3)
            prev_dx = float(prev["dx"]) if "dx" in prev else None
            dx_calc = base_m["dx"] - m["dx"]
            c["dx"] = round(prev_dx if (prev_dx is not None and abs(prev_dx) <= 20.0)
                            else max(-4.0, min(4.0, dx_calc)), 3)
        if is_ascii and "adv_scale" in prev:
            c["adv_scale"] = prev["adv_scale"]
        table[key] = c
    if save:
        _save_table(table, note=note)
    return table


_BAD_KEYS = re.compile(r"^(?:pass\d+\.ttf|(?:.*/)?工作区/.*)$")


def _save_table(table, note=None):
    """把校准值合并进 fonts/fontcal.json（保留没参与本次校准的条目）。"""
    data = {}
    try:
        with open(CAL_PATH, encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        data = {}
    fonts = data.setdefault("fonts", {})
    fonts.update(table or {})
    # 清掉历史坏键：裸 passN.ttf（ZTi-Han/ZTi-Sym 会串值）、施工目录里的路径
    for k in list(fonts):
        if _BAD_KEYS.match(k.replace(os.sep, "/")):
            del fonts[k]
    # 同一套字体只留一个键：有路径键就删掉裸 basename 键（旧表遗留）
    basenames = {k.rsplit("/", 1)[-1] for k in fonts if "/" in k}
    for k in list(fonts):
        if "/" not in k and k in basenames:
            del fonts[k]
    # 旧布局遗留：ZTi-Han / ZTi-Sym 现在只在 字体输出/ 下，仓库内 fonts/ 那份已不存在
    for k in list(fonts):
        if k.startswith(("fonts/ZTi-Han/", "fonts/ZTi-Sym/")):
            del fonts[k]
    data.pop("base", None)
    data.update({
        "ref_px": REF_PX, "base_size_pt": BASE_SIZE_PT, "scale": SCALE,
        "han_ink_em": HAN_INK_EM, "ascii_height_ratio": ASCII_HEIGHT_RATIO,
        "sample": SAMPLE, "ascii_sample": SAMPLE_ASCII,
        "note": note or ("统一基础参数：汉字墨迹高 = 字号 × %.3f，"
                         "数字 = 汉字 × %.2f；只做字号/基线对齐。"
                         % (HAN_INK_EM, ASCII_HEIGHT_RATIO)),
    })
    os.makedirs(os.path.dirname(CAL_PATH), exist_ok=True)
    with open(CAL_PATH, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    _cache_reset()
    return CAL_PATH


def recalibrate_all(save=True, note=None):
    """注册表里的字库 + 老 ZTi 回退库，全部按统一基础参数重标一遍。"""
    return calibrate(known_fonts(), _active_han_font(), save=save, note=note)


_cache = None
# 预览用的临时校准值：{cal_key: params}，不落盘，params_for 优先查它。
_overrides = {}


def set_override(path, params):
    """临时注入某套字体的校准值（效果预览用，不写 fontcal.json）。

    键用**绝对路径**：预览的字体常在 out/ 下，按 basename 会和
    ``字体输出/ZTi-Han/pass2.ttf`` 撞名，把预览值串到正式字库上。
    """
    _overrides[os.path.abspath(path)] = dict(params or {})
    _cache_reset()


def clear_override(path=None):
    """清掉一套或全部临时校准值。"""
    if path is None:
        _overrides.clear()
    else:
        _overrides.pop(os.path.abspath(path), None)
    _cache_reset()


def _cache_reset():
    global _cache, _glyph_cache
    _cache = None
    _glyph_cache = None
    _cmap_cache.clear()
    _auto_cache.clear()
    _ink_h_cache.clear()
    _pen_cache.clear()


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


def _pen_mask(path, ch, px):
    """把单个字画成 8 位掩膜，返回 numpy 数组（失败返回 None）。"""
    try:
        import numpy as np
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return None
    try:
        f = ImageFont.truetype(path, max(4, int(px)))
        l, t, r, b = f.getbbox(ch)
        pad = 12
        im = Image.new("L", (max(1, r - l) + 2 * pad,
                              max(1, b - t) + 2 * pad), 0)
        ImageDraw.Draw(im).text((pad - l, pad - t), ch, fill=255, font=f)
        return np.asarray(im)
    except Exception:
        return None


_pen_cache = {}


def pen_width_at(path, px, chars=None):
    """该字体在 px 字号下的**实际笔宽**（画布 px），量不到返回 None。

    做法：样本字画成掩膜 → 距离变换。每个墨迹像素的 2×距离就是
    「局部笔画直径」，取骨架附近（局部极大）的中位数即笔宽。

    为什么需要它：公式层的分数线/根号是按 `size_px * 0.024` 画的
    （12.4pt 下只有 2px），而手写字库的笔宽实测 4px（≈0.056 em）。
    规则线只有正文的一半粗，用户看到的就是「除号、根号太细太淡」。
    """
    if not path or not os.path.exists(path):
        return None
    px = int(round(float(px)))
    key = (os.path.abspath(path), px, chars or "")
    if key in _pen_cache:
        return _pen_cache[key]
    if chars is None:
        chars = SAMPLE_ASCII if _looks_ascii(path) else SAMPLE
    try:
        import numpy as np
        from scipy import ndimage
    except Exception:
        _pen_cache[key] = None
        return None
    vals = []
    for ch in chars:
        a = _pen_mask(path, ch, px)
        if a is None:
            continue
        mask = a > 64
        if not mask.any():
            continue
        dist = ndimage.distance_transform_edt(mask)
        local_max = ndimage.maximum_filter(dist, size=3)
        ridge = dist[(dist > 0) & (dist >= local_max - 1e-6)]
        if ridge.size:
            vals.append(float(np.median(ridge)) * 2.0)
    val = float(np.median(vals)) if vals else None
    _pen_cache[key] = val
    return val


def pen_em_ratio(path, px):
    """笔宽占字号的比值（em）；量不到时给 0.056（=4px/72px 实测值）。"""
    w = pen_width_at(path, px)
    if not w:
        return 0.056
    return max(0.02, min(0.12, w / float(max(1, int(round(px))))))


def thicken_alpha(img, delta_px, gain=1.0):
    """把小字号的笔画补回正文笔宽与墨色。

    · delta_px：单边加粗量（px），按距离场外扩，亚像素精度；
    · gain：墨色增益（1.0 = 不动）。小字按比例边缘更多，平均 alpha 天生偏低，
      补一点让「浓淡」也和正文一致。

    为什么需要：字库的笔宽随字号等比缩放——0.6 倍的角标只剩 2.4px，
    和正文 4px 的笔画一比就像换了支细笔（用户反馈「角标明显变细」）。
    """
    if img is None or (delta_px <= 0.05 and gain is not None and gain <= 1.001):
        return img
    try:
        import numpy as np
        from scipy import ndimage
        from PIL import Image
    except Exception:
        return img
    try:
        a = np.asarray(img.getchannel("A")).astype(np.float32) / 255.0
        if a.size == 0 or a.max() <= 0.02:
            return img
        if delta_px and delta_px > 0.05:
            mask = a > 0.5
            if mask.any():
                din = ndimage.distance_transform_edt(mask)
                dout = ndimage.distance_transform_edt(~mask)
                signed = np.where(mask, din - 0.5, 0.5 - dout)
                grow = np.clip(signed + 0.5 + float(delta_px), 0.0, 1.0)
                a = np.maximum(a, grow)
        if gain and gain > 1.001:
            a = np.clip(a * float(gain), 0.0, 1.0)
        out = img.copy()
        out.putalpha(Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), "L"))
        return out
    except Exception:
        return img


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


_auto_cache = {}


def _auto_params(path):
    """表里没登记时的兜底：按统一目标现场量一个 size_scale（不落盘）。

    历史 bug：自建字库建库时把校准键写成了工程目录路径（basename 撞车），
    注册后的正式路径查不到，params_for 回落 1.0 —— 汉字只有数字的 2/3 大。
    这里保证任何字体都按同一目标对齐，代价是一次测量（按文件 mtime 缓存）。
    """
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime_ns, st.st_size)
    except OSError:
        return dict(DEFAULTS)
    hit = _auto_cache.get(key)
    if hit is not None:
        return dict(hit)
    try:
        c = measure_params(path)
    except Exception:
        c = dict(DEFAULTS)
    _auto_cache[key] = dict(c)
    return dict(c)


def params_for(path):
    """取某套字体的校准参数。

    顺序：预览临时值 → 「项目相对路径」查表 → basename 查表（兼容旧表）
    → 表里没有就按统一目标现场估（_auto_params），不会静默回落 1.0。
    """
    if not path:
        return dict(DEFAULTS)
    # 预览用的临时值优先（不写盘，退出/清 cache 即失效）
    ov = _overrides.get(os.path.abspath(path))
    if ov is not None:
        p = dict(DEFAULTS)
        p.update({kk: vv for kk, vv in ov.items() if kk in DEFAULTS})
        return p
    tab = load()
    got = None
    for k in _lookup_keys(path):
        if k in tab:
            got = tab[k]
            break
    if not got:
        return _auto_params(path)
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

    if len(sys.argv) > 1 and sys.argv[1] == "measure":
        for path in known_fonts():
            cal = params_for(path)
            m = _measure(path, _sample_for(path), cal.get("size_scale", 1.0))
            print("%-52s size=%.4f 墨迹高=%.1f（目标 %.1f）"
                  % (path, cal.get("size_scale", 1.0), m["height"],
                     target_ascii_h() if _looks_ascii(path) else target_han_h()))
        sys.exit(0)

    if len(sys.argv) > 1 and sys.argv[1] == "recalibrate":
        dry = "--dry-run" in sys.argv
        table = recalibrate_all(save=not dry)
        print("%-52s %8s %8s" % ("字库（项目相对路径）", "字号倍", "字距倍"))
        for k, v in sorted(table.items()):
            print("%-52s %8.4f %8.4f"
                  % (k, v.get("size_scale", 1.0), v.get("adv_scale", 1.0)))
        print("目标：汉字 %.1fpx @ %.1fpx 字号；数字 %.1fpx"
              % (target_han_h(), REF_PX, target_ascii_h()))
        print("已写入", CAL_PATH if not dry else "（--dry-run，未写盘）")
        sys.exit(0)

    print(__doc__)
    print("用法：py -3 fontcal.py recalibrate [--dry-run]")

