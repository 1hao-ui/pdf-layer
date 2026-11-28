# -*- coding: utf-8 -*-
"""icons.py — 界面图标（沙滩主题的线性双色图标，PIL 现画，不依赖图片文件）。

为什么现画
----------
· 只有一个 exe 要打包，不想再带一堆 png；图标尺寸又随 DPI 变。
· 双色（主线 + 强调色）能跟着深浅色主题走，不用准备两套图。
· 24 单位网格、1.9 单位描边、圆头圆角，和界面里的圆润感一致。

用法
----
    img = icons.image("render", 18, fg="#322C24", accent="#0C7F8B", dpi=1.5)
    photo = icons.photo(root, "render", 18, fg, accent, dpi=1.5)   # ImageTk

    ttk.Button(bar, text=" 渲染", image=photo, compound="left")

图标清单：write / style / out / render / pdf / png / image / plus / trash /
up / down / sliders / refresh / check / ai / log / chat / save / open / dots /
minus / fit / play / gear / sun。名字不认识时返回 None（调用方退回纯文字）。
"""
from __future__ import annotations

try:
    from PIL import Image, ImageDraw, ImageTk
    PIL_OK = True
except Exception:  # pragma: no cover - 打包环境一定有 PIL
    Image = ImageDraw = ImageTk = None
    PIL_OK = False

__all__ = ["image", "photo", "names", "PIL_OK"]

# 画在 24×24 的格子里，最后再缩放；SS 是超采样倍数（边缘更干净）。
GRID = 24.0
SS = 4


class _Pen(object):
    """把 24 格坐标换算成像素的小画笔。"""

    def __init__(self, draw, scale):
        self.d = draw
        self.k = float(scale)

    def _pt(self, xy):
        return (xy[0] * self.k, xy[1] * self.k)

    def _box(self, box):
        return (box[0] * self.k, box[1] * self.k,
                box[2] * self.k, box[3] * self.k)

    def _w(self, w):
        return max(1, int(round(w * self.k)))

    def line(self, pts, color, w=1.9, caps=True):
        p = [self._pt(v) for v in pts]
        width = self._w(w)
        self.d.line(p, fill=color, width=width, joint="curve")
        if caps:
            r = width / 2.0
            for x, y in (p[0], p[-1]):
                self.d.ellipse((x - r, y - r, x + r, y + r), fill=color)

    def poly(self, pts, color, fill=None, w=1.9):
        p = [self._pt(v) for v in pts]
        self.d.polygon(p, fill=fill, outline=color,
                       width=self._w(w) if not fill else 1)

    def ellipse(self, box, color, w=1.9, fill=None):
        b = self._box(box)
        self.d.ellipse(b, outline=color, width=self._w(w), fill=fill)

    def circle(self, cx, cy, r, color, w=1.9, fill=None):
        b = self._box((cx - r, cy - r, cx + r, cy + r))
        self.d.ellipse(b, outline=color, width=self._w(w), fill=fill)

    def rrect(self, box, color, w=1.9, radius=2.5, fill=None):
        b = self._box(box)
        self.d.rounded_rectangle(b, radius=radius * self.k,
                                 outline=color,
                                 width=self._w(w) if not fill else 1,
                                 fill=fill)

    def arc(self, box, start, end, color, w=1.9):
        self.d.arc(self._box(box), start=start, end=end,
                   fill=color, width=self._w(w))

    def pieslice(self, box, start, end, fill):
        self.d.pieslice(self._box(box), start=start, end=end, fill=fill)


# --------------------------------------------------------------------- 图标
def _write(p, fg, ac):
    """书写：一支斜放的笔。"""
    p.poly([(6.2, 17.8), (8.6, 17.0), (17.4, 8.2), (16.0, 6.8)], fg, fill=fg)
    p.poly([(6.2, 17.8), (8.6, 17.0), (7.2, 15.6)], ac, fill=ac)
    p.line([(15.4, 5.4), (18.6, 8.6)], ac, 1.4)


def _style(p, fg, ac):
    """样式：调色盘 + 三个颜料点。"""
    p.ellipse((4.2, 5.2, 19.8, 18.8), fg, 1.8)
    p.circle(15.4, 14.4, 2.5, fg, 1.5)
    p.circle(8.8, 10.0, 1.5, ac, 1.2, fill=ac)
    p.circle(13.0, 8.4, 1.5, ac, 1.2, fill=ac)
    p.circle(8.6, 14.2, 1.5, ac, 1.2, fill=ac)


def _out(p, fg, ac):
    """输出：托盘 + 向上的箭头。"""
    p.line([(4.6, 13.0), (4.6, 19.2), (19.4, 19.2), (19.4, 13.0)], fg, 1.9)
    p.line([(12.0, 16.6), (12.0, 5.6)], ac, 1.9)
    p.line([(7.9, 9.6), (12.0, 5.2), (16.1, 9.6)], ac, 1.9)


def _render(p, fg, ac):
    """渲染：圆里的播放三角。"""
    p.circle(12, 12, 8.2, fg, 1.8)
    p.poly([(9.9, 8.4), (16.2, 12.0), (9.9, 15.6)], ac, fill=ac)


def _pdf(p, fg, ac):
    """导出 PDF：文档 + 向下的箭头。"""
    p.rrect((5.4, 3.4, 18.6, 20.6), fg, 1.8, 2.2)
    p.line([(8.4, 8.0), (13.2, 8.0)], fg, 1.5)
    p.line([(8.4, 11.2), (15.6, 11.2)], fg, 1.5)
    p.line([(14.6, 13.8), (14.6, 18.6)], ac, 1.8)
    p.line([(12.2, 16.2), (14.6, 18.8), (17.0, 16.2)], ac, 1.8)


def _png(p, fg, ac):
    """导出 PNG：相框 + 山 + 向下箭头。"""
    p.rrect((3.8, 4.6, 16.4, 15.6), fg, 1.8, 2.0)
    p.circle(7.6, 8.2, 1.3, ac, 1.0, fill=ac)
    p.line([(4.6, 13.8), (8.4, 9.8), (11.2, 12.6), (13.4, 10.2),
            (15.8, 12.6)], ac, 1.7)
    p.line([(19.0, 12.2), (19.0, 19.2)], ac, 1.8)
    p.line([(16.6, 16.8), (19.0, 19.4), (21.4, 16.8)], ac, 1.8)


def _image(p, fg, ac):
    """背景：相框 + 山。"""
    p.rrect((3.6, 5.0, 20.4, 19.0), fg, 1.8, 2.2)
    p.circle(8.4, 9.4, 1.5, ac, 1.1, fill=ac)
    p.line([(5.0, 16.6), (9.6, 11.6), (12.8, 14.8), (15.6, 11.8),
            (19.2, 16.0)], ac, 1.8)


def _plus(p, fg, ac):
    p.line([(12, 5.2), (12, 18.8)], fg, 2.1)
    p.line([(5.2, 12), (18.8, 12)], fg, 2.1)


def _trash(p, fg, ac):
    """删除：垃圾桶（盖 + 桶身 + 两道竖线）。"""
    p.line([(4.6, 7.0), (19.4, 7.0)], fg, 1.9)
    p.line([(9.2, 4.6), (14.8, 4.6)], fg, 1.9)
    p.rrect((6.6, 7.0, 17.4, 19.6), fg, 1.8, 1.6)
    p.line([(10.4, 10.6), (10.4, 16.4)], ac, 1.6)
    p.line([(13.6, 10.6), (13.6, 16.4)], ac, 1.6)


def _up(p, fg, ac):
    p.line([(6.6, 14.4), (12.0, 9.0), (17.4, 14.4)], fg, 2.0)


def _left(p, fg, ac):
    p.line([(14.4, 6.6), (9.0, 12.0), (14.4, 17.4)], fg, 2.0)


def _right(p, fg, ac):
    p.line([(9.6, 6.6), (15.0, 12.0), (9.6, 17.4)], fg, 2.0)


def _scan(p, fg, ac):
    """自动划区：四角取景框 + 中间一条扫描线。"""
    for x, y, sx, sy in ((3.8, 3.8, 1, 1), (20.2, 3.8, -1, 1),
                         (3.8, 20.2, 1, -1), (20.2, 20.2, -1, -1)):
        p.line([(x, y + sy * 5.0), (x, y), (x + sx * 5.0, y)], fg, 1.9)
    p.line([(8.0, 12.0), (16.0, 12.0)], ac, 1.8)


def _eye(p, fg, ac):
    p.ellipse((3.4, 6.6, 20.6, 17.4), fg, 1.8)
    p.circle(12, 12, 3.0, ac, 1.4, fill=ac)


def _down(p, fg, ac):
    p.line([(6.6, 9.6), (12.0, 15.0), (17.4, 9.6)], fg, 2.0)


def _sliders(p, fg, ac):
    for y, kx in ((7.2, 14.4), (12.0, 9.0), (16.8, 15.2)):
        p.line([(4.6, y), (19.4, y)], fg, 1.6)
        p.circle(kx, y, 2.1, ac, 1.4, fill=ac)


def _refresh(p, fg, ac):
    p.arc((5.0, 5.0, 19.0, 19.0), 40, 315, fg, 1.9)
    p.line([(15.6, 3.4), (18.8, 6.6), (15.0, 8.6)], ac, 1.8)


def _check(p, fg, ac):
    p.line([(5.4, 12.4), (10.0, 17.0), (18.6, 7.4)], ac, 2.4)


def _ai(p, fg, ac):
    """AI：一大一小两颗四角星。"""
    def star(cx, cy, r, color, fill=True):
        pts = [(cx, cy - r), (cx + 0.3 * r, cy - 0.3 * r), (cx + r, cy),
               (cx + 0.3 * r, cy + 0.3 * r), (cx, cy + r),
               (cx - 0.3 * r, cy + 0.3 * r), (cx - r, cy),
               (cx - 0.3 * r, cy - 0.3 * r)]
        p.poly(pts, color, fill=color if fill else None, w=1.4)
    star(10.2, 9.8, 6.4, ac)
    star(17.8, 16.6, 3.4, fg)


def _log(p, fg, ac):
    """日志：终端窗口。"""
    p.rrect((3.6, 5.0, 20.4, 19.0), fg, 1.8, 2.4)
    p.line([(7.4, 9.4), (10.4, 12.0), (7.4, 14.6)], ac, 1.9)
    p.line([(12.6, 15.0), (16.8, 15.0)], ac, 1.9)


def _chat(p, fg, ac):
    """反馈：对话气泡 + 三个点。"""
    p.rrect((3.8, 4.8, 20.2, 16.2), fg, 1.8, 3.0)
    p.poly([(7.8, 16.0), (7.8, 19.8), (11.8, 16.0)], fg, fill=fg)
    for x in (9.0, 12.0, 15.0):
        p.circle(x, 10.5, 1.25, ac, 1.0, fill=ac)


def _save(p, fg, ac):
    """保存项目：软盘。"""
    p.rrect((4.2, 4.2, 19.8, 19.8), fg, 1.8, 2.0)
    p.rrect((8.2, 4.2, 15.8, 9.6), fg, 1.5, 1.2)
    p.rrect((7.4, 12.4, 16.6, 19.8), ac, 1.5, 1.2)


def _open(p, fg, ac):
    """打开项目：文件夹。"""
    p.line([(3.8, 9.4), (3.8, 6.4), (9.6, 6.4), (11.8, 9.0)], fg, 1.8)
    p.rrect((3.8, 9.0, 20.2, 19.4), fg, 1.8, 1.8)
    p.line([(3.8, 12.2), (20.2, 12.2)], ac, 1.6)


def _dots(p, fg, ac):
    for x in (6.4, 12.0, 17.6):
        p.circle(x, 12, 1.7, fg, 1.2, fill=fg)


def _minus(p, fg, ac):
    p.line([(5.4, 12), (18.6, 12)], fg, 2.1)


def _fit(p, fg, ac):
    """适应窗口：四角括号。"""
    for x, y, sx, sy in ((4.6, 4.6, 1, 1), (19.4, 4.6, -1, 1),
                         (4.6, 19.4, 1, -1), (19.4, 19.4, -1, -1)):
        p.line([(x, y + sy * 5.2), (x, y), (x + sx * 5.2, y)], fg, 1.9)


def _play(p, fg, ac):
    p.poly([(7.6, 5.4), (18.4, 12.0), (7.6, 18.6)], ac, fill=ac)


def _gear(p, fg, ac):
    p.circle(12, 12, 6.4, fg, 1.8)
    p.circle(12, 12, 2.4, ac, 1.5, fill=ac)
    import math as _m
    for i in range(8):
        a = i * _m.pi / 4
        p.line([(12 + 7.6 * _m.cos(a), 12 + 7.6 * _m.sin(a)),
                (12 + 9.4 * _m.cos(a), 12 + 9.4 * _m.sin(a))], fg, 1.7)


def _palm(p, fg, ac):
    """品牌标：一棵椰子树（弯曲的树干 + 5 片下垂的叶 + 2 颗椰子）。"""
    p.line([(13.0, 21.0), (12.2, 16.0), (12.4, 11.2)], fg, 2.4)
    p.line([(12.4, 11.2), (9.0, 8.2), (4.6, 8.9)], ac, 2.0)
    p.line([(12.4, 11.2), (9.6, 11.4), (5.2, 13.7)], ac, 2.0)
    p.line([(12.4, 11.2), (11.8, 6.7), (9.3, 4.0)], ac, 2.0)
    p.line([(12.4, 11.2), (14.9, 6.9), (18.5, 5.4)], ac, 2.0)
    p.line([(12.4, 11.2), (15.7, 10.7), (19.5, 13.1)], ac, 2.0)
    p.circle(11.3, 11.9, 1.1, fg, 1.1, fill=fg)
    p.circle(13.6, 12.1, 1.1, fg, 1.1, fill=fg)


def _sun(p, fg, ac):
    """深浅色：半月。"""
    p.circle(12, 12, 7.2, fg, 1.8)
    p.pieslice((5.4, 5.4, 18.6, 18.6), -90, 90, ac)
    p.circle(12, 12, 7.2, fg, 1.8)


ICONS = {
    "write": _write, "style": _style, "out": _out, "render": _render,
    "pdf": _pdf, "png": _png, "image": _image, "plus": _plus,
    "trash": _trash, "up": _up, "down": _down, "sliders": _sliders,
    "refresh": _refresh, "check": _check, "ai": _ai, "log": _log,
    "chat": _chat, "save": _save, "open": _open, "dots": _dots,
    "minus": _minus, "fit": _fit, "play": _play, "gear": _gear,
    "sun": _sun, "left": _left, "right": _right, "scan": _scan,
    "palm": _palm,
    "eye": _eye,
}


def names():
    return sorted(ICONS)


# --------------------------------------------------------------------- 对外
def image(name, size=18, fg="#322C24", accent="#0C7F8B", dpi=1.0):
    """画一张图标（PIL.Image，RGBA）。name 不认识时返回 None。"""
    fn = ICONS.get(name)
    if fn is None or not PIL_OK:
        return None
    px = max(10, int(round(float(size) * max(0.5, float(dpi or 1.0)) * SS)))
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    pen = _Pen(ImageDraw.Draw(img), px / GRID)
    try:
        fn(pen, fg, accent)
    except Exception:
        return None
    out = max(10, int(round(float(size) * max(0.5, float(dpi or 1.0)))))
    return img.resize((out, out), Image.LANCZOS)


def photo(master, name, size=18, fg="#322C24", accent="#0C7F8B", dpi=1.0):
    """画一张 ImageTk.PhotoImage（给 ttk/tk 控件用）。"""
    img = image(name, size=size, fg=fg, accent=accent, dpi=dpi)
    if img is None or ImageTk is None:
        return None
    return ImageTk.PhotoImage(img, master=master)


if __name__ == "__main__":  # 手动看图标：py -3 icons.py → icons_preview.png
    if not PIL_OK:
        raise SystemExit("没有 PIL")
    cell = 64
    cols = 5
    rows = (len(ICONS) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * cell), (246, 237, 221))
    draw = ImageDraw.Draw(sheet)
    for i, name in enumerate(sorted(ICONS)):
        img = image(name, 40, "#322C24", "#0C7F8B", 1.0)
        x = (i % cols) * cell
        y = (i // cols) * cell
        sheet.paste(img, (x + 12, y + 10), img)
        draw.text((x + 8, y + 50), name, fill=(60, 50, 40))
    sheet.save("icons_preview.png")
    print("icons_preview.png", sheet.size, len(ICONS), "icons")
