# -*- coding: utf-8 -*-
"""preview.py — 效果示例：建库前先看「写出来是什么样」。

两种预览
--------
1. **整段样文**（``compare``）：同一段文字，用「新建字库」和「当前字库」
   各排一遍，1:1 并排，直接比观感（不放大，避免比例失真）。
2. **单字对照**（``glyph_compare``）：同一批字，上下两行，看笔画粗细、
   字面大小、基线是否一致。

关键点：预览用的是真实渲染链路（hwrender），不是另画一套。校准值通过
``fontcal.set_override`` 临时注入，不会写盘；预览完自动清掉。
"""
from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont


def _label_font(px=13):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


def _flat(bg_rgb=(255, 255, 255)):
    return Image.new("RGBA", (1, 1), bg_rgb + (255,))


def render_block(font_paths, text, size_pt=12.4, width_pt=404,
                 cal=None, seed=20261118, jitter=1.0, fallback=True):
    """用给定字库渲染一段文字，返回 RGBA 图（和生成器同一个引擎）。

    ``fallback=True`` 时把注册表里其它已启用汉字库作为缺字回退，
    这样预览出来就是主界面真实的输出效果（缺字不会凭空消失）。
    """
    import hwrender as H
    font_paths = [p for p in (font_paths or []) if p and os.path.exists(p)]
    if not font_paths:
        raise ValueError("没有可用的字体文件")
    main = font_paths[0]
    variants = font_paths[1:]
    overrides = {}
    if cal:
        import fontcal
        for p, params in cal.items():
            overrides[p] = params
            fontcal.set_override(p, params)
    try:
        fb = []
        if fallback:
            try:
                from . import registry
                fb = registry.fallback_fonts("han", exclude=main)
            except Exception:
                fb = []
        hand = H.Hand(main, size_pt=size_pt, seed=seed, jitter=jitter,
                      variant_fonts=variants, han_fallback_fonts=fb)
        return hand.render_block(text, width_pt=width_pt)
    finally:
        if overrides:
            import fontcal
            for p in overrides:
                fontcal.clear_override(p)


def compare(candidate, current, text, out_png, size_pt=12.4, width_pt=404,
            labels=("自建字库", "当前字库"), cal=None):
    """上下两栏并排预览，返回输出路径。

    candidate/current 是字体路径列表（第一个当主字体）。
    注意：render_block 返回的图是**像素**，w/h 是 **pt**；画布一律按像素拼，
    混用会让第二栏被裁掉（旧版就是这样）。
    """
    blocks = []
    for paths in (candidate, current):
        try:
            img, w, h = render_block(paths, text, size_pt=size_pt,
                                     width_pt=width_pt, cal=cal)
            blocks.append((img, w, h, ""))
        except Exception as e:      # noqa: BLE001
            blocks.append((None, 0, 0, repr(e)))
    pad = 16
    label_h = 22
    widths = [b[0].width if b[0] is not None else 360 for b in blocks]
    wmax = max(widths) + pad * 2
    htotal = sum((b[0].height if b[0] is not None else 30) + pad + label_h
                 for b in blocks) + pad
    canvas = Image.new("RGBA", (max(360, wmax), htotal), (255, 255, 255, 255))
    d = ImageDraw.Draw(canvas)
    lf = _label_font(13)
    y = 8
    for (img, w, h, err), lab in zip(blocks, labels):
        d.text((pad, y), lab, font=lf, fill=(60, 60, 60, 255))
        y += label_h
        if img is not None:
            canvas.alpha_composite(img, (pad, y))
            y += img.height
        else:
            d.text((pad, y + 6), "渲染失败：%s" % err, font=lf,
                   fill=(190, 40, 40, 255))
            y += 30
        y += pad
        d.line((pad, y - 8, canvas.width - pad, y - 8),
               fill=(228, 228, 228, 255))
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    canvas.convert("RGB").save(out_png)
    return out_png


def glyph_compare(candidate_font, current_font, chars, out_png,
                  size_pt=36, cols=16):
    """同一批字上下对照：候选 / 当前。"""
    chars = [c for c in (chars or "") if c.strip()][:cols * 6]
    if not chars:
        raise ValueError("没有要对照的字")
    cell = int(size_pt * 1.9)
    rows = (len(chars) + cols - 1) // cols
    canvas = Image.new("RGB", (cols * cell + 80, rows * cell * 2 + 40),
                       "white")
    d = ImageDraw.Draw(canvas)
    lf = _label_font(13)
    d.text((6, 6), "上：新建字库　下：当前字库", font=lf, fill=(70, 70, 70))
    try:
        fc = ImageFont.truetype(candidate_font, int(size_pt))
    except Exception:
        fc = None
    try:
        fo = ImageFont.truetype(current_font, int(size_pt))
    except Exception:
        fo = None
    for i, ch in enumerate(chars):
        c, r = i % cols, i // cols
        x0 = 6 + c * cell
        for row, font, ybase in ((0, fc, 30), (1, fo, 30 + rows * cell)):
            y0 = ybase + r * cell
            d.rectangle((x0, y0, x0 + cell, y0 + cell),
                        outline=(238, 238, 238))
            if font is None:
                continue
            try:
                bb = d.textbbox((0, 0), ch, font=font)
                d.text((x0 + (cell - (bb[2] - bb[0])) / 2 - bb[0],
                        y0 + (cell - (bb[3] - bb[1])) / 2 - bb[1]),
                       ch, font=font, fill=(20, 20, 22))
            except Exception:
                continue
    os.makedirs(os.path.dirname(os.path.abspath(out_png)), exist_ok=True)
    canvas.save(out_png)
    return out_png


SAMPLE = ("三相电路中，相电压等于线电压除以根号三。电阻R=10Ω，电流I=220/5=44A。"
          "有功功率P=3×44²×3≈17.4kW，功率因数cosφ≈0.999。")


def current_fonts():
    """当前启用字库的字体路径（主字体在前）。"""
    try:
        import symbols
        main = symbols.han_main_font()
        out = [main] if main else []
        out += [p for p in symbols._han_variant_fonts()]
        return [p for p in out if p]
    except Exception:
        return []


def candidate_fonts(lib_dir):
    """某个字库目录里的 pass*.ttf（pass2 优先）。"""
    try:
        from . import registry
        names = registry.discover_passes(lib_dir)
    except Exception:
        names = sorted(n for n in os.listdir(lib_dir)
                       if n.startswith("pass") and n.endswith(".ttf"))
    return [os.path.join(lib_dir, n) for n in names]
