# -*- coding: utf-8 -*-
"""重画 ∑/Σ/∏：垂直居中在数学轴线（0.25em），并支持按内容高度拉伸。

问题：上一版把 ∑ 画成 y=0.05..0.93em，墨迹中心在 0.49em，
而数学运算符应该以 0.25em（轴线）为中心 —— 所以看着「太高」。

正确做法：让字形中心落在 0.25em，即墨迹从 -0.19em 到 0.69em（高 0.88em）。
大运算符本来就该略低于基线（∫ 更明显），这才是手写/印刷的常态。
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

AXIS_EM = 0.25          # 数学轴线位置（em，相对基线）
SUM_H_EM = 0.88         # ∑ 墨迹高度（em）
PROD_H_EM = 0.88

SUM_PATHS = [
    [(0.06, 0.05), (0.94, 0.05), (0.94, 0.20)],      # 上横 + 右端下钩
    [(0.12, 0.20), (0.70, 0.50), (0.06, 0.95)],      # 主折线
    [(0.06, 0.95), (0.94, 0.95), (0.94, 0.80)],      # 下横 + 右端上钩
]
PROD_PATHS = [
    [(0.06, 0.05), (0.94, 0.05)],
    [(0.24, 0.05), (0.24, 0.95)],
    [(0.76, 0.05), (0.76, 0.95)],
]


def stroke_polygon(pts, width, seg=12):
    half = width / 2.0
    n = len(pts)
    if n < 2:
        return []
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
        else:
            nx = normals[i - 1][0] + normals[i][0]
            ny = normals[i - 1][1] + normals[i][1]
            L = max(1e-9, math.hypot(nx, ny))
            nx, ny = nx / L, ny / L
            cosang = max(0.35, abs(normals[i - 1][0] * nx + normals[i - 1][1] * ny))
            nx, ny = nx / cosang, ny / cosang
        left.append((pts[i][0] + nx * half, pts[i][1] + ny * half))
        right.append((pts[i][0] - nx * half, pts[i][1] - ny * half))
    poly = list(left)
    ex, ey = pts[-1]
    a0 = math.atan2(normals[-1][1], normals[-1][0])
    for k in range(seg + 1):
        a = a0 + math.pi * k / seg
        poly.append((ex + half * math.cos(a), ey + half * math.sin(a)))
    poly += list(reversed(right))
    sx, sy = pts[0]
    a1 = math.atan2(-normals[0][1], -normals[0][0])
    for k in range(seg + 1):
        a = a1 + math.pi * k / seg
        poly.append((sx + half * math.cos(a), sy + half * math.sin(a)))
    return poly


def build(paths, units=1000, ink_w=0.62, ink_h=SUM_H_EM, pen=78, axis=AXIS_EM):
    """生成字形；垂直方向让墨迹中心落在 axis（em，相对基线）。"""
    w, h = units * ink_w, units * ink_h
    ox = units * (1.0 - ink_w) / 2.0
    # 墨迹底部位置（字体坐标，向上为正）：中心在 axis*units
    bottom = axis * units - h / 2.0
    tp = TTGlyphPen(None)
    for p in paths:
        pts = [(x * w + ox, bottom + (1.0 - y) * h) for x, y in p]
        poly = stroke_polygon(pts, pen)
        if not poly:
            continue
        tp.moveTo(poly[0])
        for q in poly[1:]:
            tp.lineTo(q)
        tp.closePath()
    return tp.glyph()


def patch(path):
    f = TTFont(path)
    cmap = f.getBestCmap()
    glyf, hmtx = f["glyf"], f["hmtx"]
    order = list(f.getGlyphOrder())
    done = []
    for ch, paths, adv in (("∑", SUM_PATHS, 0.70), ("Σ", SUM_PATHS, 0.70),
                           ("∏", PROD_PATHS, 0.70)):
        gname = cmap.get(ord(ch))
        if gname is None or gname not in glyf.glyphs:
            continue
        try:
            glyf[gname] = build(paths)
            hmtx.metrics[gname] = (int(round(1000 * adv)), 70)
            done.append(ch)
        except Exception as e:
            print("  %s 失败 %r" % (ch, e))
    f.setGlyphOrder(order)
    out = path.replace(".ttf", "-axis.ttf")
    f.save(out)
    return out, done


def main():
    for name in ("pass1.ttf", "pass2.ttf", "pass3.ttf", "pass4.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        try:
            out, ch = patch(p)
            print("%-12s -> %-18s %r" % (name, os.path.basename(out), ch))
        except Exception as e:
            import traceback
            print("%-12s FAIL %r" % (name, e))
            traceback.print_exc()

    # 对照：∑ 与字母并排，看垂直位置
    rows = []
    for name in ("pass2.ttf", "pass2-axis.ttf"):
        p = os.path.join(SYM, name)
        if not os.path.exists(p):
            continue
        im = Image.new("RGB", (900, 230), (255, 255, 255))
        d = ImageDraw.Draw(im)
        f = ImageFont.truetype(p, 100)
        d.text((10, 30), "∑ P   ∏ x   ∫ f", font=f, fill=(20, 20, 20))
        # 画基线与轴线参考
        asc = f.getmetrics()[0]
        y0 = 30
        d.line([(0, y0 + asc), (890, y0 + asc)], fill=(200, 200, 255), width=1)
        d.line([(0, y0 + asc - int(0.25 * 100)), (890, y0 + asc - int(0.25 * 100))],
               fill=(255, 200, 200), width=1)
        rows.append((name, im))

    if rows:
        lab = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 16)
        W = max(r[1].width for r in rows) + 20
        H = sum(r[1].height + 26 for r in rows)
        cv = Image.new("RGB", (W, H), (245, 245, 245))
        d = ImageDraw.Draw(cv)
        y = 4
        for nm, im in rows:
            d.text((8, y), nm + "（蓝=基线 红=轴线）", font=lab, fill=(150, 0, 0))
            y += 22
            cv.paste(im, (8, y))
            y += im.height + 4
        cv.save(os.path.join(ROOT, "out", "sum_axis.png"))
        print("\nsaved out/sum_axis.png")


if __name__ == "__main__":
    main()
