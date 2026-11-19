# -*- coding: utf-8 -*-
"""修正带下伸部的字符：把尾巴放回基线以下。

根因：scanfont 建库时把每块墨迹的「底边」当基线。对带下伸部的字符
（φ ψ ρ γ η μ β ζ χ g p q y j）来说，墨迹底边是尾巴末端，
对齐到基线后整个字形被抬高——看起来像上标（用户反馈 ,cosφ 的 φ 飘上去）。

对照正常字体：o 的底边距基线 +2，φ / g 是 +48（约 0.24em）。
所以把这些字符整体下移 0.24em 即可。
"""
import os

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SYM = os.path.abspath(os.path.join(ROOT, "..", "字体输出", "ZTi-Sym"))

DESCENDERS = "φψργημβζχgpqyj"
DROP = 240          # 下移量（字体单位，upem=1000）≈ 0.24em，与正常字体一致


def shift_glyph(font, gname, dy):
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
    upem = f["head"].unitsPerEm or 1000
    dy = -DROP * upem / 1000.0
    done = []
    for ch in DESCENDERS:
        gname = cmap.get(ord(ch))
        if gname is None or gname not in glyf.glyphs:
            continue
        try:
            if getattr(glyf[gname], "numberOfContours", 0) == 0:
                continue
            glyf[gname] = shift_glyph(f, gname, dy)
            done.append(ch)
        except Exception as e:
            print("  %s 失败 %r" % (ch, e))
    out = path.replace(".ttf", "-desc.ttf")
    f.save(out)
    return out, done


def main():
    for name in ("pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        try:
            out, done = patch(p)
            print("%-12s -> %-18s 下移 %d 个: %s"
                  % (name, os.path.basename(out), len(done), "".join(done)))
        except Exception as e:
            import traceback
            print("%-12s FAIL %r" % (name, e))
            traceback.print_exc()

    # 验证 + 对照图
    rows = []
    for name in ("pass2.ttf", "pass2-desc.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        f = ImageFont.truetype(p, 200)
        asc = f.getmetrics()[0]
        print("\n%s 底边距基线：" % name)
        print("  " + "  ".join("%s:%+d" % (c, f.getbbox(c)[3] - asc)
                               for c in "ocosφgpy"))
        im = Image.new("RGB", (900, 260), (255, 255, 255))
        d = ImageDraw.Draw(im)
        f2 = ImageFont.truetype(p, 90)
        d.text((10, 20), ",cosφ  gap  φγη  gpy", font=f2, fill=(20, 20, 20))
        asc2 = f2.getmetrics()[0]
        d.line([(0, 20 + asc2), (890, 20 + asc2)], fill=(150, 180, 255), width=1)
        rows.append((name, im))

    if rows:
        lab = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
        W = max(r[1].width for r in rows) + 20
        H = sum(r[1].height + 26 for r in rows)
        cv = Image.new("RGB", (W, H), (245, 245, 245))
        d = ImageDraw.Draw(cv)
        y = 4
        for nm, im in rows:
            d.text((8, y), nm + "（蓝=基线）", font=lab, fill=(150, 0, 0))
            y += 22
            cv.paste(im, (8, y))
            y += im.height + 4
        cv.save(os.path.join(ROOT, "out", "descender_fix.png"))
        print("\nsaved out/descender_fix.png")


if __name__ == "__main__":
    main()
