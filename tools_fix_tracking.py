# -*- coding: utf-8 -*-
"""按字形宽度自适应字距：窄字（i = 1 等）多留白，宽字（数字/字母）少留白。

问题：为了让 i=1 不粘连，给全部 204 字加了 90 单位留白，
结果数字串 380 / 22 / 17.4 之间空得明显，看着像「3 8 0」。

改法：留白量与该字墨迹宽度成反比——
  · 窄字（墨迹 < 0.25em，如 i l 1 = -）：+130
  · 中等（0.25~0.5em）：+70
  · 宽字（> 0.5em，如 0 8 a 等）：+40
这样 i=1 仍然分得开，数字串又保持紧凑。
"""
import os

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SYM = os.path.abspath(os.path.join(ROOT, "..", "字体输出", "ZTi-Sym"))


def bearing_for(ink_units):
    """按墨迹宽度（字体单位，upem=1000）决定留白。"""
    if ink_units < 250:
        return 130
    if ink_units < 500:
        return 70
    return 40


def shift_glyph(font, gname, dx):
    gs = font.getGlyphSet()
    rec = DecomposingRecordingPen(gs)
    gs[gname].draw(rec)
    tp = TTGlyphPen(gs)
    rec.replay(TransformPen(tp, (1, 0, 0, 1, dx, 0)))
    return tp.glyph()


def patch(path):
    """重建字距：先还原到原始 advance，再按宽度加合适的留白。

    注意：原字体已经被加过 90 单位留白，这里需要先减掉旧的再加新的。
    """
    f = TTFont(path)
    glyf, hmtx = f["glyf"], f["hmtx"]
    order = list(f.getGlyphOrder())
    upem = f["head"].unitsPerEm or 1000
    OLD_SIDE = 90
    n = 0
    for gname in order:
        if gname not in glyf.glyphs:
            continue
        adv, lsb = hmtx.metrics.get(gname, (0, 0))
        if adv <= 0:
            continue
        g = glyf[gname]
        if getattr(g, "numberOfContours", 0) == 0:
            continue
        # 量墨迹宽（从字形轮廓的 xMin/xMax）
        try:
            g.recalcBounds(glyf)
            ink = max(0, g.xMax - g.xMin)
        except Exception:
            ink = adv
        new_side = bearing_for(ink * 1000.0 / upem)
        delta = new_side - OLD_SIDE
        if abs(delta) < 1:
            continue
        try:
            glyf[gname] = shift_glyph(f, gname, delta / 2.0)
            hmtx.metrics[gname] = (int(round(adv + delta)), lsb)
            n += 1
        except Exception:
            pass
    f.setGlyphOrder(order)
    out = path.replace(".ttf", "-track.ttf")
    f.save(out)
    return out, n


def main():
    for name in ("pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        try:
            out, n = patch(p)
            print("%-12s -> %-20s 调整 %d 字" % (name, os.path.basename(out), n))
        except Exception as e:
            import traceback
            print("%-12s FAIL %r" % (name, e))
            traceback.print_exc()

    rows = []
    for name in ("pass2.ttf", "pass2-track.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        im = Image.new("RGB", (900, 240), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.text((10, 20), "380 22 17.4", font=ImageFont.truetype(p, 90), fill=(20, 20, 20))
        d.text((10, 130), "i=1  a+b  52.1", font=ImageFont.truetype(p, 90), fill=(20, 20, 20))
        rows.append((name, im))

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
        cv.save(os.path.join(ROOT, "out", "track_fix.png"))
        print("\nsaved out/track_fix.png")


if __name__ == "__main__":
    main()
