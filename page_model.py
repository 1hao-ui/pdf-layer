# -*- coding: utf-8 -*-
"""page_model.py — 页面 / 段落模型 + 背景导入 + 拼合导出。

GUI 与 AI 接口共用这一层，保证「所见即所得」：
    · 背景：空白 A4 / 图片（扫描件、手账底图）/ 已有 PDF 的某一页；
    · 段落：每段有自己的矩形区域（pt，页面坐标）、字号、行距、对齐；
    · 渲染：逐段 render_block 后贴到页面对应矩形，返回整页 RGBA（SCALE=4）；
    · 导出：PNG / PDF（有源 PDF 时原样保留原内容，只把手写层叠上去）。

坐标为 pt（1pt = 1/72 英寸），内部像素 = pt × hwrender.SCALE。
"""
from __future__ import annotations

import io
import json
import math
import os
from dataclasses import dataclass, field, asdict

from PIL import Image

import hwrender as H

SCALE = H.SCALE                      # 4 px/pt
A4_W, A4_H = 595.0, 842.0
MM = 72.0 / 25.4                     # 1mm 多少 pt


# ---------------------------------------------------------------- 纸张
# 纸张尺寸：键 → (宽 mm, 高 mm)；纵向为默认，横向把宽高对调。
PAPER_SIZES = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
    "B5": (176.0, 250.0),
    "A5": (148.0, 210.0),
    "Letter": (215.9, 279.4),
    "16K": (184.0, 260.0),
}
PAPER_SIZE_ORDER = ("A4", "A3", "B5", "A5", "Letter", "16K")

# 纸张材质：底色 + 纹理强度。纹理很轻，只负责让纸「有纤维感」，
# 不能抢正文墨迹的对比度；纯白走零开销分支，和旧版逐像素一致。
PAPER_MATERIALS = {
    "clean": {"label": "纯白", "base": (255, 255, 255),
              "grain": 0.0, "blotch": 0.0, "fiber": 0.0, "fleck": 0},
    "warm": {"label": "米白", "base": (250, 246, 235),
             "grain": 1.8, "blotch": 1.2, "fiber": 0.0, "fleck": 0},
    "ivory": {"label": "象牙", "base": (244, 233, 206),
              "grain": 2.0, "blotch": 1.5, "fiber": 0.0, "fleck": 0},
    "kraft": {"label": "牛皮", "base": (211, 183, 143),
              "grain": 2.8, "blotch": 2.2, "fiber": 2.0, "fleck": 0},
    "recycled": {"label": "再生", "base": (233, 232, 225),
                 "grain": 2.2, "blotch": 1.2, "fiber": 0.0, "fleck": 1},
    "rice": {"label": "宣纸", "base": (247, 241, 222),
             "grain": 2.4, "blotch": 1.1, "fiber": 3.0, "fleck": 0},
}
PAPER_MATERIAL_ORDER = ("clean", "warm", "ivory", "kraft", "recycled", "rice")


def paper_size_pt(size="A4", landscape=False):
    """纸张尺寸 → (宽 pt, 高 pt)；未知尺寸回退 A4。

    A4 沿用项目既有的 595×842（旧版和基线都按这个值），其余按 mm 换算。
    """
    key = str(size) if str(size) in PAPER_SIZES else "A4"
    if key == "A4":
        w, h = A4_W, A4_H
    else:
        w_mm, h_mm = PAPER_SIZES[key]
        w, h = w_mm * MM, h_mm * MM
    return (h, w) if landscape else (w, h)


def paper_size_label(size="A4", landscape=False):
    """下拉框显示名：`A4 210×297`（横向时写 297×210）。"""
    key = str(size) if str(size) in PAPER_SIZES else "A4"
    w_mm, h_mm = PAPER_SIZES[key]
    if landscape:
        w_mm, h_mm = h_mm, w_mm
    return "%s  %.0f×%.0f" % (key, w_mm, h_mm)


def paper_size_for(width_pt, height_pt):
    """按页面尺寸反查预设纸张（老项目没有 paper 字段时用）；认不出返回 A4。"""
    for key in PAPER_SIZE_ORDER:
        for land in (False, True):
            w, h = paper_size_pt(key, land)
            if abs(w - width_pt) < 1.0 and abs(h - height_pt) < 1.0:
                return key
    return "A4"


def make_paper_image(width_pt, height_pt, material="clean", seed=20261126):
    """生成一张纸质底图（RGBA）。

    纹理在半分辨率上生成再放大：A4 × SCALE=4 也只有 2MP，切换材质不卡；
    颗粒/纤维都控制在几个灰阶，近看有纸感、远看仍是干净的纸。
    """
    w = max(1, int(round(width_pt * SCALE)))
    h = max(1, int(round(height_pt * SCALE)))
    spec = PAPER_MATERIALS.get(str(material)) or PAPER_MATERIALS["clean"]
    base = tuple(spec["base"])
    if spec["grain"] <= 0 and spec["blotch"] <= 0:
        return Image.new("RGBA", (w, h), base + (255,))

    import numpy as np
    from PIL import ImageDraw

    # 纹理先画一半大小，最后放大：省 4 倍内存/时间，纸纹本身就是软的。
    sw, sh = max(2, w // 2), max(2, h // 2)
    rng = np.random.default_rng(int(seed) + sum(base))
    arr = np.empty((sh, sw, 3), dtype=np.float32)
    arr[...] = base

    if spec["blotch"] > 0:
        bw, bh = max(2, sw // 96), max(2, sh // 96)
        low = rng.normal(0.0, 1.0, (bh, bw)).astype(np.float32)
        low = np.asarray(Image.fromarray(low, mode="F").resize(
            (sw, sh), Image.BICUBIC), dtype=np.float32)
        arr += low[..., None] * float(spec["blotch"])

    if spec["grain"] > 0:
        gw, gh = max(2, sw // 2), max(2, sh // 2)
        grain = rng.normal(0.0, 1.0, (gh, gw)).astype(np.float32)
        grain = np.asarray(Image.fromarray(grain, mode="F").resize(
            (sw, sh), Image.NEAREST), dtype=np.float32)
        arr += grain[..., None] * float(spec["grain"])

    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")

    # 纤维 / 杂点画在叠加层上：宣纸是浅色长纤维，牛皮是深色短毛，
    # 再生纸是极少量深色纸屑。都只用于「看近处有材质」，不影响读字。
    fiber = float(spec.get("fiber") or 0)
    fleck = int(spec.get("fleck") or 0)
    if fiber > 0 or fleck > 0:
        layer = Image.new("L", (sw, sh), 0)
        pen = ImageDraw.Draw(layer)
        if fiber > 0:
            n = int(sw * sh / 9000.0 * fiber)
            light = material == "rice"
            for _ in range(n):
                x = rng.uniform(0, sw)
                y = rng.uniform(0, sh)
                ln = rng.uniform(6, 26) * (1.0 if light else 0.6)
                ang = rng.uniform(-0.35, 0.35)
                x2, y2 = x + ln * math.cos(ang), y + ln * math.sin(ang)
                val = int(rng.uniform(6, 14)) if light else int(rng.uniform(10, 22))
                pen.line((x, y, x2, y2), fill=val, width=1)
        if fleck > 0:
            n = int(sw * sh / 26000.0 * fleck)
            for _ in range(n):
                x = rng.uniform(0, sw)
                y = rng.uniform(0, sh)
                r = rng.uniform(0.6, 2.4)
                pen.ellipse((x - r, y - r, x + r, y + r),
                            fill=int(rng.uniform(8, 20)))
        # 用叠加层的灰度做加减：宣纸的亮纤维提亮，其它纸的暗纤维压暗。
        arr = np.asarray(im, dtype=np.int16)
        l = np.asarray(layer, dtype=np.int16)
        if material == "rice":
            arr = arr + l[..., None]
        else:
            arr = arr - l[..., None]
        im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), mode="RGB")

    im = im.resize((w, h), Image.BICUBIC)
    return im.convert("RGBA")


# ---------------------------------------------------------------- 段落
@dataclass
class Paragraph:
    """一段手写文字 + 它的书写区域。"""
    text: str = ""
    rect: list = field(default_factory=lambda: [56.0, 120.0, 539.0, 400.0])
    size_pt: float = H.DEFAULT_SIZE_PT
    line_pt: float = H.DEFAULT_LINE_PT
    align: str = "left"              # left / center / right
    jitter: float = 1.0
    seed: int = 20261118
    ink: tuple = H.INK
    # 渲染后统计（不进 spec）
    used_size: tuple = (0.0, 0.0)

    @property
    def left(self):
        return min(self.rect[0], self.rect[2])

    @property
    def top(self):
        return min(self.rect[1], self.rect[3])

    @property
    def right(self):
        return max(self.rect[0], self.rect[2])

    @property
    def bottom(self):
        return max(self.rect[1], self.rect[3])

    @property
    def width(self):
        return max(1.0, self.right - self.left)

    @property
    def height(self):
        return max(1.0, self.bottom - self.top)

    def normalize(self):
        l, t, r, b = self.left, self.top, self.right, self.bottom
        self.rect = [round(l, 2), round(t, 2), round(r, 2), round(b, 2)]
        return self


# ---------------------------------------------------------------- 背景
def load_image_background(path, page_w=A4_W, page_h=A4_H):
    """图片按「等比铺满页面、不裁切」缩放到页面大小；返回 RGBA 像素图（SCALE）。"""
    im = Image.open(path).convert("RGBA")
    pw, ph = int(round(page_w * SCALE)), int(round(page_h * SCALE))
    k = min(pw / im.width, ph / im.height)
    im = im.resize((max(1, int(im.width * k)), max(1, int(im.height * k))),
                   Image.LANCZOS)
    canvas = Image.new("RGBA", (pw, ph), (255, 255, 255, 255))
    canvas.alpha_composite(im, ((pw - im.width) // 2, (ph - im.height) // 2))
    return canvas


def load_pdf_background(path, page_index=0, dpi=200, page_w=A4_W, page_h=A4_H):
    """把 PDF 的某一页渲染成页面尺寸的位图背景。

    返回 (背景 RGBA, 页面尺寸 (w_pt, h_pt), 页数)；页面尺寸用 PDF 自己的。
    """
    import pymupdf as fitz
    doc = fitz.open(path)
    if not (0 <= page_index < len(doc)):
        raise ValueError("PDF 页码越界：%d（共 %d 页）" % (page_index, len(doc)))
    page = doc[page_index]
    pw, ph = page.rect.width, page.rect.height
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples).convert("RGBA")
    pw_px, ph_px = int(round(pw * SCALE)), int(round(ph * SCALE))
    if im.size != (pw_px, ph_px):
        im = im.resize((pw_px, ph_px), Image.LANCZOS)
    doc.close()
    return im, (pw, ph), page_index


# ---------------------------------------------------------------- 页面 / 文档
@dataclass
class Page:
    width_pt: float = A4_W
    height_pt: float = A4_H
    paragraphs: list = field(default_factory=list)
    bg_kind: str = "blank"            # blank / image / pdf
    bg_path: str = ""
    bg_page: int = 0
    bg_dpi: int = 200
    paper_size: str = "A4"            # 纸张尺寸键（PAPER_SIZES）
    paper_landscape: bool = False     # 横向
    paper_kind: str = "clean"         # 纸张材质键（PAPER_MATERIALS）
    _bg: object = None                # 缓存：PIL RGBA
    source_pdf: str = ""              # 导出 PDF 时把整页当作底图
    _warn: list = field(default_factory=list)

    # ---- 纸张 ----
    def set_paper(self, size=None, landscape=None, material=None, resize=True):
        """改纸张设置；空白页会按新尺寸重设页面大小并让底图失效。

        图片 / PDF 背景的页面尺寸由来源决定，切纸张只记录偏好，不动页面尺寸。
        """
        if size is not None and str(size) in PAPER_SIZES:
            self.paper_size = str(size)
        if landscape is not None:
            self.paper_landscape = bool(landscape)
        if material is not None and str(material) in PAPER_MATERIALS:
            self.paper_kind = str(material)
        if resize and self.bg_kind == "blank":
            self.width_pt, self.height_pt = paper_size_pt(
                self.paper_size, self.paper_landscape)
        self.invalidate()
        return self

    @property
    def paper_material(self):
        """当前材质的显示名（纯白 / 米白 / …）。"""
        return (PAPER_MATERIALS.get(self.paper_kind)
                or PAPER_MATERIALS["clean"])["label"]

    def paper_is_plain(self):
        """是不是「纯白空白纸」——导出 PDF 时可以省掉背景图。"""
        return self.bg_kind == "blank" and self.paper_kind == "clean"

    def background(self):
        if self._bg is None:
            if self.bg_kind == "image" and self.bg_path and os.path.exists(self.bg_path):
                self._bg = load_image_background(self.bg_path, self.width_pt, self.height_pt)
            elif self.bg_kind == "pdf" and self.bg_path and os.path.exists(self.bg_path):
                self._bg, size, _ = load_pdf_background(
                    self.bg_path, self.bg_page, self.bg_dpi)
                self.width_pt, self.height_pt = size
            else:
                self._bg = make_paper_image(
                    self.width_pt, self.height_pt, self.paper_kind)
        return self._bg

    def invalidate(self):
        self._bg = None
        self._warn = []

    def add_paragraph(self, paragraph=None, **kw):
        p = paragraph or Paragraph(**kw)
        if not p.rect:
            top = 120.0 + 40.0 * len(self.paragraphs)
            p.rect = [56.0, top, self.width_pt - 56.0, min(self.height_pt - 60.0, top + 260.0)]
        if p.size_pt is None:
            p.size_pt = H.DEFAULT_SIZE_PT
        if p.line_pt is None:
            p.line_pt = H.DEFAULT_LINE_PT
        self.paragraphs.append(p)
        return p


def make_hand(paragraph, hand_factory=None, **overrides):
    """按段落参数造 Hand。hand_factory(paragraph) 可完全接管。"""
    if hand_factory is not None:
        return hand_factory(paragraph)
    import symbols
    params = dict(size_pt=paragraph.size_pt, ink=tuple(paragraph.ink),
                  jitter=paragraph.jitter, seed=paragraph.seed)
    params.update(overrides)
    if symbols.han_available():
        return symbols.make_han_hand(**params)
    return H.Hand(symbols.han_main_font() if symbols.han_available()
                  else symbols.font_file(), **params)


def render_page(page, hand_factory=None, draw_frames=False):
    """把页面上所有段落渲染成整页 RGBA（含背景，可选段落调试边框）。返回 (page, img)。"""
    from PIL.ImageDraw import Draw
    page.invalidate()
    img = page.background().copy()
    draw = Draw(img)
    for i, p in enumerate(page.paragraphs):
        if not (p.text or "").strip():
            continue
        hand = make_hand(p, hand_factory)
        block, w_pt, h_pt = hand.render_block(p.text, width_pt=p.width,
                                              line_pt=p.line_pt)
        p.used_size = (w_pt, h_pt)
        x = p.left
        if p.align == "center":
            x = p.left + (p.width - w_pt) / 2.0
        elif p.align == "right":
            x = p.right - w_pt
        ox = int(round(x * SCALE))
        oy = int(round(p.top * SCALE))
        img.alpha_composite(block, (ox, oy))
        if h_pt > p.height + 1.0:
            page._warn.append("第 %d 段超出框高 %.1fpt（框 %.1fpt），已照常绘制"
                              % (i + 1, h_pt, p.height))
        if draw_frames:
            draw.rectangle([p.left * SCALE, p.top * SCALE, p.right * SCALE,
                           p.bottom * SCALE],
                           outline=(255, 120, 120, 120), width=2)
    return page, img


def pdf_page_count(path):
    """PDF 页数（GUI 页码控件 / AI 入参校验用）。"""
    import pymupdf as fitz
    doc = fitz.open(path)
    n = len(doc)
    doc.close()
    return n


def pdf_page_size(path, page_index=0):
    """PDF 某页尺寸 (w_pt, h_pt)。"""
    import pymupdf as fitz
    doc = fitz.open(path)
    r = doc[page_index].rect
    doc.close()
    return (r.width, r.height)


# ---------------------------------------------------------------- 导出
def export_png(page, path, with_frames=False):
    _, img = render_page(page, draw_frames=with_frames)
    out = Image.new("RGB", img.size, (255, 255, 255))
    out.paste(img.convert("RGB"), (0, 0))
    out.save(path)
    return path


def handwriting_layer(page, path=None):
    """只含手写笔迹的透明整页层（导出 PDF 时盖上原 PDF 用）。"""
    img = Image.new("RGBA", page.background().size, (0, 0, 0, 0))
    for p in page.paragraphs:
        if not (p.text or "").strip():
            continue
        hand = make_hand(p)
        block, w_pt, h_pt = hand.render_block(p.text, width_pt=p.width,
                                              line_pt=p.line_pt)
        x = p.left
        if p.align == "center":
            x = p.left + (p.width - w_pt) / 2.0
        elif p.align == "right":
            x = p.right - w_pt
        img.alpha_composite(block, (int(round(x * SCALE)), int(round(p.top * SCALE))))
    if path:
        img.save(path)
    return img


def export_pdf(page, path):
    """导出 PDF：源 PDF 就地叠手写层；否则新建页面（背景图 + 手写层）。

    也可传 Page 列表（多页）：要求都来自同一个 source_pdf，按各页 bg_page 叠加，
    这样「导入一份 PDF → 多页分别书写 → 导出一份 PDF」不会丢原内容。
    """
    import pymupdf as fitz
    pages = page if isinstance(page, (list, tuple)) else [page]
    srcs = {p.source_pdf for p in pages}
    if len(pages) > 1:
        if len(srcs) != 1 or not next(iter(srcs)):
            raise ValueError("多页导出要求每页都来自同一个 source_pdf")
        src = next(iter(srcs))
        doc = fitz.open(src)
        for p in pages:
            idx = max(0, min(len(doc) - 1, int(p.bg_page)))
            layer = handwriting_layer(p)
            buf = io.BytesIO()
            layer.save(buf, format="PNG")
            pg = doc[idx]
            pg.insert_image(pg.rect, stream=buf.getvalue(), overlay=True,
                            keep_proportion=False)
        doc.save(path, deflate=True, garbage=3)
        doc.close()
        return path
    page = pages[0]
    layer = handwriting_layer(page)
    buf = io.BytesIO()
    layer.save(buf, format="PNG")
    png = buf.getvalue()
    if page.source_pdf and os.path.exists(page.source_pdf):
        doc = fitz.open(page.source_pdf)
        pg = doc[page.bg_page if page.bg_page < len(doc) else 0]
        pg.insert_image(pg.rect, stream=png, overlay=True,
                        keep_proportion=False)
        doc.save(path, deflate=True, garbage=3)
        doc.close()
        return path
    doc = fitz.open()
    rect = fitz.Rect(0, 0, page.width_pt, page.height_pt)
    pg = doc.new_page(width=page.width_pt, height=page.height_pt)
    if not page.paper_is_plain():
        bg = io.BytesIO()
        page.background().convert("RGB").save(bg, format="PNG")
        pg.insert_image(rect, stream=bg.getvalue(), overlay=False,
                        keep_proportion=False)
    pg.insert_image(rect, stream=png, overlay=True, keep_proportion=False)
    doc.save(path, deflate=True, garbage=3)
    doc.close()
    return path


# ---------------------------------------------------------------- spec（JSON）
def page_to_spec(page):
    return {
        "width_pt": page.width_pt,
        "height_pt": page.height_pt,
        "paper": {"size": page.paper_size,
                  "landscape": bool(page.paper_landscape),
                  "material": page.paper_kind},
        "bg": {"kind": page.bg_kind, "path": page.bg_path,
               "page": page.bg_page, "dpi": page.bg_dpi},
        "source_pdf": page.source_pdf,
        "paragraphs": [
            {"text": p.text, "rect": list(p.rect), "size_pt": p.size_pt,
             "line_pt": p.line_pt, "align": p.align, "jitter": p.jitter,
             "seed": p.seed, "ink": list(p.ink)}
            for p in page.paragraphs
        ],
    }


def page_from_spec(spec):
    bg = spec.get("bg") or {}
    paper = spec.get("paper") or {}
    width = float(spec.get("width_pt") or 0) or None
    height = float(spec.get("height_pt") or 0) or None
    size = paper.get("size")
    landscape = paper.get("landscape")
    if not (size or landscape is not None) and width and height:
        # 老项目没有 paper 字段：按尺寸反查，认不出就记 A4。
        size = paper_size_for(width, height)
    size = size if size in PAPER_SIZES else "A4"
    landscape = bool(landscape)
    pw, ph = paper_size_pt(size, landscape)
    page = Page(width_pt=width or pw,
                height_pt=height or ph,
                bg_kind=bg.get("kind") or "blank",
                bg_path=bg.get("path") or "",
                bg_page=int(bg.get("page") or 0),
                bg_dpi=int(bg.get("dpi") or 200),
                paper_size=size,
                paper_landscape=landscape,
                paper_kind=(paper.get("material")
                            if paper.get("material") in PAPER_MATERIALS
                            else "clean"),
                source_pdf=spec.get("source_pdf") or "")
    for ps in spec.get("paragraphs") or []:
        page.paragraphs.append(Paragraph(
            text=ps.get("text") or "",
            rect=[float(v) for v in ps.get("rect") or [56, 120, 539, 400]],
            size_pt=float(ps.get("size_pt") or H.DEFAULT_SIZE_PT),
            line_pt=float(ps.get("line_pt") or H.DEFAULT_LINE_PT),
            align=ps.get("align") or "left",
            jitter=float(ps.get("jitter") if ps.get("jitter") is not None else 1.0),
            seed=int(ps.get("seed") or 20261118),
            ink=tuple(ps.get("ink") or H.INK),
        ))
    return page


def render_spec(spec, hand_factory=None):
    """一次性：spec → (page, 整页 RGBA, 警告列表)。"""
    page = page_from_spec(spec)
    page, img = render_page(page, hand_factory)
    return page, img, list(page._warn)


if __name__ == "__main__":
    # 自测：空白页 + 两段，导出 PNG/PDF 到 out/
    import sys
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
    os.makedirs(out, exist_ok=True)
    pg = Page()
    pg.add_paragraph(Paragraph(
        text="三相电路的暂态过程与铁芯损耗分析。", rect=[56, 100, 539, 220]))
    pg.add_paragraph(Paragraph(
        text="解：由换路定则，电感电流不能跃变。", rect=[56, 240, 539, 380]))
    pg, img = render_page(pg)
    print("page", img.size, "warn", pg._warn)
    export_png(pg, os.path.join(out, "page_model_demo.png"))
    export_pdf(pg, os.path.join(out, "page_model_demo.pdf"))
    print("->", out)
