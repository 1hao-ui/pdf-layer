# -*- coding: utf-8 -*-
"""重画 ∑/Σ 字形（单轮廓描边 + 圆头笔道），并给所有字形加字距。

上一版的问题：每段线段各画一个矩形、端点半圆又各画一个多边形，
结果轮廓互相独立、填充出现怪异凸起。改成「一条折线 = 一个闭合轮廓」：
沿折线算左右偏移点，两端加半圆，得到一个干净的多边形。

∑ 的正确结构（从印刷体逐行采样得到）：
  上横（占满宽）+ 右端向下小钩
  主折线：从左上（上横左端下方）斜向右中，再斜回左下
  下横（占满宽）+ 右端向上小钩
  主折线末端正好落在下横左端，视觉上连成一体。
"""
import math
import os

from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.abspath(__file__))
SYM = os.path.abspath(os.path.join(ROOT, "..", "字体输出", "ZTi-Sym"))

SIDE = 90            # 每个字形额外加的左右留白总和（upem=1000）

# ∑ 笔道（归一化 0..1，y 向下）
SUM_PATHS = [
    [(0.06, 0.05), (0.94, 0.05), (0.94, 0.20)],      # 上横 + 右端下钩
    [(0.12, 0.20), (0.70, 0.50), (0.06, 0.95)],      # 主折线（左中右中左）
    [(0.06, 0.95), (0.94, 0.95), (0.94, 0.80)],      # 下横 + 右端上钩
]


def stroke_polygon(pts, width, seg=12):
    """一条折线 -> 一个闭合多边形（左右偏移 + 两端半圆）。"""
    half = width / 2.0
    n = len(pts)
    if n < 2:
        return []
    # 每段法向
    normals = []
    for i in range(n - 1):
        dx, dy = pts[i + 1][0] - pts[i][0], pts[i + 1][1] - pts[i][1]
        L = max(1e-9, math.hypot(dx, dy))
        normals.append((-dy / L, dx / L))

    left, right = [], []
    for i in range(n):
        if i == 0:
            nx, ny = normals[0]
        elif i == n - 1:
            nx, ny = normals[-1]
        else:                                   # 拐点：两条法向取平均（斜接）
            nx = normals[i - 1][0] + normals[i][0]
            ny = normals[i - 1][1] + normals[i][1]
            L = max(1e-9, math.hypot(nx, ny))
            nx, ny = nx / L, ny / L
            # 斜接补偿：夹角越尖，偏移要越远，否则拐角变细
            cosang = max(0.35, abs(normals[i - 1][0] * nx + normals[i - 1][1] * ny))
            k = 1.0 / cosang
            nx, ny = nx * k, ny * k
        left.append((pts[i][0] + nx * half, pts[i][1] + ny * half))
        right.append((pts[i][0] - nx * half, pts[i][1] - ny * half))

    poly = list(left)
    # 末端半圆
    ex, ey = pts[-1]
    a0 = math.atan2(normals[-1][1], normals[-1][0])
    for k in range(seg + 1):
        a = a0 + math.pi * k / seg
        poly.append((ex + half * math.cos(a), ey + half * math.sin(a)))
    poly += list(reversed(right))
    # 起点半圆
    sx, sy = pts[0]
    a1 = math.atan2(-normals[0][1], -normals[0][0])
    for k in range(seg + 1):
        a = a1 + math.pi * k / seg
        poly.append((sx + half * math.cos(a), sy + half * math.sin(a)))
    return poly


def build_sum(units=1000, ink_w=0.62, ink_h=0.88, pen=78):
    w, h = units * ink_w, units * ink_h
    ox = units * (1.0 - ink_w) / 2.0
    oy = units * 0.05
    tp = TTGlyphPen(None)
    for path in SUM_PATHS:
        pts = [(x * w + ox, oy + (1.0 - y) * h) for x, y in path]   # y 翻转
        poly = stroke_polygon(pts, pen)
        if not poly:
            continue
        tp.moveTo(poly[0])
        for p in poly[1:]:
            tp.lineTo(p)
        tp.closePath()
    return tp.glyph()


def add_side_bearing(font, gname, extra):
    gs = font.getGlyphSet()
    rec = DecomposingRecordingPen(gs)
    gs[gname].draw(rec)
    tp = TTGlyphPen(gs)
    rec.replay(TransformPen(tp, (1, 0, 0, 1, extra / 2.0, 0)))
    return tp.glyph()


def patch(path):
    f = TTFont(path)
    cmap = f.getBestCmap()
    glyf, hmtx = f["glyf"], f["hmtx"]
    order = list(f.getGlyphOrder())
    nb = 0
    for gname in order:
        if gname not in glyf.glyphs:
            continue
        adv, lsb = hmtx.metrics.get(gname, (0, 0))
        if adv <= 0:
            continue
        try:
            if getattr(glyf[gname], "numberOfContours", 0) != 0:
                glyf[gname] = add_side_bearing(f, gname, SIDE)
                hmtx.metrics[gname] = (int(round(adv + SIDE)), lsb)
                nb += 1
        except Exception:
            pass
    ns = 0
    for ch in ("∑", "Σ"):
        gname = cmap.get(ord(ch))
        if gname is None or gname not in glyf.glyphs:
            continue
        try:
            glyf[gname] = build_sum()
            hmtx.metrics[gname] = (int(round(1000 * 0.70)), 70)
            ns += 1
        except Exception as e:
            print("  ∑ 失败 %r" % e)
    f.setGlyphOrder(order)
    out = path.replace(".ttf", "-v3.ttf")
    f.save(out)
    return out, nb, ns


def main():
    for name in ("pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        try:
            out, nb, ns = patch(p)
            print("%-12s -> %-18s 留白 %d 字, ∑ %d" % (name, os.path.basename(out), nb, ns))
        except Exception as e:
            import traceback
            print("%-12s FAIL %r" % (name, e))
            traceback.print_exc()

    rows = []
    for name in ("pass2.ttf", "pass2-v3.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        im = Image.new("RGB", (880, 300), (255, 255, 255))
        d = ImageDraw.Draw(im)
        d.text((10, 10), "∑ i=1  n=52.1", font=ImageFont.truetype(p, 110), fill=(20, 20, 20))
        d.text((10, 150), "小字：i=1 a+b 3^11", font=ImageFont.truetype(p, 44), fill=(20, 20, 20))
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
        cv.save(os.path.join(ROOT, "out", "symfont_v3.png"))
        print("\nsaved out/symfont_v3.png")


if __name__ == "__main__":
    main()
