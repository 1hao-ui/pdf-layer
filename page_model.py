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
import os
from dataclasses import dataclass, field, asdict

from PIL import Image

import hwrender as H

SCALE = H.SCALE                      # 4 px/pt
A4_W, A4_H = 595.0, 842.0
MM = 72.0 / 25.4                     # 1mm 多少 pt


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
    _bg: object = None                # 缓存：PIL RGBA
    source_pdf: str = ""              # 导出 PDF 时把整页当作底图
    _warn: list = field(default_factory=list)

    def background(self):
        if self._bg is None:
            if self.bg_kind == "image" and self.bg_path and os.path.exists(self.bg_path):
                self._bg = load_image_background(self.bg_path, self.width_pt, self.height_pt)
            elif self.bg_kind == "pdf" and self.bg_path and os.path.exists(self.bg_path):
                self._bg, size, _ = load_pdf_background(
                    self.bg_path, self.bg_page, self.bg_dpi)
                self.width_pt, self.height_pt = size
            else:
                self._bg = Image.new("RGBA",
                                     (int(self.width_pt * SCALE), int(self.height_pt * SCALE)),
                                     (255, 255, 255, 255))
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
    if page.bg_kind != "blank":
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
    page = Page(width_pt=float(spec.get("width_pt") or A4_W),
                height_pt=float(spec.get("height_pt") or A4_H),
                bg_kind=bg.get("kind") or "blank",
                bg_path=bg.get("path") or "",
                bg_page=int(bg.get("page") or 0),
                bg_dpi=int(bg.get("dpi") or 200),
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
