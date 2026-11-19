# -*- coding: utf-8 -*-
"""把「中线类」符号抬到数学轴线位置（修 - 被当成 _ 的问题）。

根因：scanfont 建库时把每块墨迹的底边当基线，导致本来画在格子中部的
符号（- − · = + × ÷ ~ 等）全被压到基线附近。实测：

    字符   中心相对基线（upem=1000，200px 测量换算）
    =      +21.5
    -      +6.0     <- 和 _ 的 +5.0 几乎一样，所以 - 看起来就是 _
    _      +5.0
    +      +31.5
    数学轴线应为 +50（0.25em）

改法：按字符类别把轮廓整体上移，使墨迹中心落在轴线附近。
  · 短横/点类（- − · · ） 上移 ~44
  · 等号/加减/乘除/波浪  上移 ~28
不改字距、不改其它字形。
"""
import os

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SYM = os.path.abspath(os.path.join(ROOT, "..", "字体输出", "ZTi-Sym"))

# 字符 -> 上移量（字体单位，upem=1000）
# 数值 = 目标中心(500) - 实测中心，按 200px 测量结果换算（×5）
RAISE = {
    "-": 220, "−": 220, "·": 220, "•": 220, "⋅": 220,
    "=": 145, "+": 95, "×": 120, "÷": 95, "~": 140, "≈": 140, "≡": 145,
    "∓": 95, "±": 95,
}

# 中线类符号的参考目标：中心落在 0.5em（轴线）
TARGET_CENTER = 500


def raise_glyph(font, gname, dy):
    gs = font.getGlyphSet()
    rec = DecomposingRecordingPen(gs)
    gs[gname].draw(rec)
    tp = TTGlyphPen(gs)
    rec.replay(TransformPen(tp, (1, 0, 0, 1, 0, dy)))
    return tp.glyph()


def patch(path):
    f = TTFont(path)
    cmap = f.getBestCmap()
    glyf = f["glyf"]
    done = []
    for ch, dy in RAISE.items():
        gname = cmap.get(ord(ch))
        if gname is None or gname not in glyf.glyphs:
            continue
        try:
            if getattr(glyf[gname], "numberOfContours", 0) == 0:
                continue
            glyf[gname] = raise_glyph(f, gname, dy)
            done.append(ch)
        except Exception as e:
            print("  %s 失败 %r" % (ch, e))
    out = path.replace(".ttf", "-mid.ttf")
    f.save(out)
    return out, done


def main():
    for name in ("pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        try:
            out, ch = patch(p)
            print("%-12s -> %-18s 抬高 %d 个: %r" % (name, os.path.basename(out), len(ch), ch))
        except Exception as e:
            import traceback
            print("%-12s FAIL %r" % (name, e))
            traceback.print_exc()

    # 对照图 + 复测
    rows = []
    for name in ("pass2.ttf", "pass2-mid.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        im = Image.new("RGB", (900, 240), (255, 255, 255))
        d = ImageDraw.Draw(im)
        f90 = ImageFont.truetype(p, 90)
        d.text((10, 20), "a-b = c+d  i=1", font=f90, fill=(20, 20, 20))
        d.text((10, 130), "a_b  1-2  ≈ ±  ×  ÷", font=f90, fill=(20, 20, 20))
        rows.append((name, im))

        # 复测
        f = ImageFont.truetype(p, 200)
        asc = f.getmetrics()[0]
        vals = []
        for ch in "=-_+":
            bb = f.getbbox(ch)
            cy = (bb[1] + bb[3]) / 2.0
            vals.append("%s:%+.0f" % (ch, asc - cy))
        print("   %-16s 中心相对基线 %s" % (name, "  ".join(vals)))

    if rows:
        lab = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
        W = max(r[1].width for r in rows) + 20
        H = sum(r[1].height + 26 for r in rows)
        cv = Image.new("RGB", (W, H), (245, 245, 245))
        d = ImageDraw.Draw(cv)
        y = 4
        for nm, im in rows:
            d.text((8, y), nm, font=lab, fill=(150, 0, 0))
            y += 22
            cv.paste(im, (8, y))
            y += im.height + 4
        cv.save(os.path.join(ROOT, "out", "midline_fix.png"))
        print("\nsaved out/midline_fix.png")


if __name__ == "__main__":
    main()
