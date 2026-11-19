# -*- coding: utf-8 -*-
"""逐字校准：为「每个字体 × 每个字符」算出各自的缩放系数。

思路
----
同一字符在 4 套字库里高度差可达 30%（真人不同遍书写大小不一）。
按套统一缩放只能对齐「该套平均字高」，对齐不了「同一个字」。

所以改成 per-glyph 校准：
  · 目标：每个字符在每套里的墨迹高度 = 该字符在所有套里的中位高 × 统一目标
  · 存储：fonts/fontcal_glyph.json  { 字体名: { 字符: 缩放系数 } }
  · 渲染时 _char_tile 先查逐字系数，没有再退回按套系数
"""
import json
import os
import statistics
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.join(ROOT, "pdf-layer")
sys.path.insert(0, PROJ)

from PIL import Image, ImageDraw, ImageFont

SYM = os.path.abspath(os.path.join(ROOT, "..", "字体输出", "ZTi-Sym"))
OUT = os.path.join(PROJ, "fonts", "fontcal_glyph.json")
REF_PX = 64

# 要覆盖的字符：数字 + 拉丁 + 希腊 + 常用符号
CHARS = (
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "αβγδεζηθικλμνξοπρστυφχψω"
    "ΓΔΘΛΞΠΣΦΨΩ"
    "+-−×÷=≠≈≡<>≤≥±∓·√∞∂∇∑∏∫∬"
    "()[]{}|/\\.,;:!?°℃%‰²³"
)


def ink_height(path, ch, px):
    try:
        f = ImageFont.truetype(path, px)
        bb = f.getbbox(ch)
    except Exception:
        return None
    if bb is None:
        return None
    w, h = max(1, bb[2] - bb[0]) + 16, max(1, bb[3] - bb[1]) + 16
    im = Image.new("L", (w, h), 0)
    ImageDraw.Draw(im).text((8 - bb[0], 8 - bb[1]), ch, fill=255, font=f)
    bb2 = im.getbbox()
    if bb2 is None:
        return None
    return bb2[3] - bb2[1]


def main():
    paths = {i: os.path.join(SYM, "pass%d.ttf" % i) for i in (1, 2, 3, 4)}
    paths = {k: v for k, v in paths.items() if os.path.exists(v)}
    if not paths:
        print("找不到字体")
        return

    # 1) 先量各套原始字高
    raw = {}
    for i, p in paths.items():
        raw[i] = {}
        for ch in CHARS:
            h = ink_height(p, ch, REF_PX)
            if h:
                raw[i][ch] = h

    # 2) 每个字符的目标高度 = 各套中位高
    table = {os.path.basename(paths[i]): {} for i in paths}
    stat = []
    for ch in CHARS:
        hs = [raw[i][ch] for i in paths if ch in raw.get(i, {})]
        if len(hs) < 2:
            continue
        target = statistics.median(hs)
        for i in paths:
            h = raw[i].get(ch)
            if not h:
                continue
            k = target / h
            # 夹一下，避免某个字被放大到离谱
            k = max(0.7, min(1.6, k))
            table[os.path.basename(paths[i])][ch] = round(k, 4)
        stat.append((ch, max(hs) - min(hs), target))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump({"ref_px": REF_PX,
                   "note": "逐字缩放：让同一字符在 4 套字库里高度一致",
                   "fonts": table}, fp, ensure_ascii=False, indent=1)

    n = sum(len(v) for v in table.values())
    print("写入 %s：%d 个（字体,字符）组合" % (OUT, n))
    for name, d in table.items():
        ks = list(d.values())
        print("  %-12s %3d 字，缩放 %.2f~%.2f" % (name, len(ks), min(ks), max(ks)))

    # 3) 验证：校准后同字高度是否一致
    print("\n校准后同一字符的高度（应基本一致）:")
    for ch in "258Pθ":
        vals = []
        for i, p in paths.items():
            k = table[os.path.basename(p)].get(ch, 1.0)
            h = raw[i].get(ch)
            if h:
                vals.append(h * k)
        if vals:
            print("  %r: %s  极差 %.0f%%" % (
                ch, "  ".join("%.0f" % v for v in vals),
                100 * (max(vals) - min(vals)) / max(1, max(vals))))


if __name__ == "__main__":
    main()
