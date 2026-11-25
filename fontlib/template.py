# -*- coding: utf-8 -*-
"""template.py — 分页的 A4 书写模板（想写多少字都行）。

旧模板的毛病
------------
`scanfont.py template` 把整张字表塞进**一页**：字一多，格子就被压到 1pt。
322 个符号还能挤下，6763 个常用字就不可能了。

这里改成真分页：先按目标格宽算每页几行几列，再按「遍 → 页」切分字表，
每页只画本页的字。产出的 ``layout.json`` 带 ``pages`` 映射，切字时按
「第几张图 = 第几遍第几页」取对应的字，不会错位。

版面约定（与 intake.py 共用，改动必须两边一起改）
    A4 595×842pt；四角 14pt 黑方块，中心距页边 22+7=29pt；
    网格 left=42 top=78 right=553 bottom=800；格内 pad=1.5pt。
"""
from __future__ import annotations

import json
import math
import os

PAGE_W, PAGE_H = 595.0, 842.0          # A4, pt
MARGIN, MARK = 22.0, 14.0              # 四角定位方块
GRID_LEFT, GRID_TOP = 42.0, 78.0
GRID_RIGHT, GRID_BOTTOM = 553.0, 800.0
PAD = 1.5
DEFAULT_CELL_PT = 32.0                 # ≈11.3mm
DEFAULT_PASSES = 2
DPI = 300

_MM = 72.0 / 25.4


def cell_pt_from_mm(mm):
    return max(12.0, float(mm) * _MM)


def geometry(cell_pt=DEFAULT_CELL_PT):
    """按格宽算列数/行数/每页格数。"""
    cell_pt = max(12.0, float(cell_pt))
    cols = max(1, int((GRID_RIGHT - GRID_LEFT) // cell_pt))
    rows = max(1, int((GRID_BOTTOM - GRID_TOP) // cell_pt))
    cell_w = (GRID_RIGHT - GRID_LEFT) / cols
    cell_h = (GRID_BOTTOM - GRID_TOP) / rows
    return {
        "left": GRID_LEFT, "top": GRID_TOP,
        "right": GRID_RIGHT, "bottom": GRID_BOTTOM,
        "cols": cols, "rows": rows, "per_page": cols * rows,
        "cell_w": round(cell_w, 4), "cell_h": round(cell_h, 4),
        "pad": PAD, "cell_pt": round(min(cell_w, cell_h), 4),
    }


# ---------------------------------------------------------------- 布局
def make_layout(chars, passes=DEFAULT_PASSES, cell_pt=DEFAULT_CELL_PT,
                title="手写模板", show_reference=True, show_index=True,
                dpi=DPI, note=""):
    """生成模板布局（不画 PDF，先算清楚每页放哪些字）。"""
    chars = [c for c in (chars or "") if c and not c.isspace()] \
        if isinstance(chars, str) else list(chars or [])
    g = geometry(cell_pt)
    per_page = g["per_page"]
    pages_per_pass = max(1, int(math.ceil(len(chars) / float(per_page))))
    pages = []
    for p in range(1, passes + 1):
        for k in range(pages_per_pass):
            start = k * per_page
            end = min(len(chars), start + per_page)
            pages.append({
                "pass": p, "page": k + 1, "start": start, "end": end,
                "chars": chars[start:end],
                "blank": max(0, per_page - (end - start)),
            })
    return {
        "version": 2,
        "title": title,
        "note": note,
        "page": {"w": PAGE_W, "h": PAGE_H, "dpi": dpi},
        "grid": g,
        "markers": {"margin": MARGIN, "size": MARK},
        "passes": passes,
        "pages_per_pass": pages_per_pass,
        "chars": chars,
        "count": len(chars),
        "pages": pages,
        "show_reference": bool(show_reference),
        "show_index": bool(show_index),
    }


def page_map(layout):
    """全局页序号 → (遍, 页内序号, 起始字)。切扫描件时按这个对齐。"""
    out = []
    for i, p in enumerate(layout.get("pages") or []):
        out.append({"index": i, "pass": p["pass"], "page": p["page"],
                    "start": p["start"], "end": p["end"]})
    return out


def chars_for_page(layout, page_index):
    pages = layout.get("pages") or []
    if 0 <= page_index < len(pages):
        return list(pages[page_index].get("chars") or [])
    return []


# ---------------------------------------------------------------- 画 PDF
def _cell_xy(grid, col, row):
    return (grid["left"] + col * grid["cell_w"],
            grid["top"] + row * grid["cell_h"])


def build_pdf(layout, out_pdf, progress=None):
    """按布局画 PDF；返回输出路径。"""
    import fitz

    pages = layout.get("pages") or []
    if not pages:
        raise ValueError("字表为空，没有可画的页")
    g = layout["grid"]
    cols, rows = g["cols"], g["rows"]
    passes = layout.get("passes", 1)
    pages_per_pass = layout.get("pages_per_pass", 1)
    total = layout.get("count", len(layout.get("chars") or []))
    title = layout.get("title") or "手写模板"
    show_ref = layout.get("show_reference", True)
    show_idx = layout.get("show_index", True)

    doc = fitz.open()
    for pi, page_info in enumerate(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        pno, k = page_info["pass"], page_info["page"]
        # 页眉
        page.insert_text((GRID_LEFT, 48),
                         "%s　第 %d 遍 · 第 %d/%d 页 · 共 %d 字" % (
                             title, pno, k, pages_per_pass, total),
                         fontname="china-s", fontsize=10.5,
                         color=(0.12, 0.12, 0.12))
        page.insert_text((GRID_LEFT, 64),
                         "用深色中性笔（0.5mm）每格写一个字，写满格、居中；"
                         "浅灰参考字不要描；四角黑块不要裁掉。",
                         fontname="china-s", fontsize=7.6,
                         color=(0.45, 0.45, 0.45))
        # 四角定位块
        for (x, y) in ((MARGIN, MARGIN),
                       (PAGE_W - MARGIN - MARK, MARGIN),
                       (MARGIN, PAGE_H - MARGIN - MARK),
                       (PAGE_W - MARGIN - MARK, PAGE_H - MARGIN - MARK)):
            page.draw_rect(fitz.Rect(x, y, x + MARK, y + MARK),
                           color=None, fill=(0, 0, 0))
        # 页脚
        page.insert_text((GRID_LEFT, PAGE_H - 18),
                         "第 %d 遍 / 第 %d 页　|　格 %d × %d　|　"
                         "整页扫描/导出，不要裁边、不要缩放" % (pno, k, cols, rows),
                         fontname="helv", fontsize=6.8, color=(0.5, 0.5, 0.5))

        chars = page_info.get("chars") or []
        for i, ch in enumerate(chars):
            col, row = i % cols, i // cols
            x0, y0 = _cell_xy(g, col, row)
            x1, y1 = x0 + g["cell_w"], y0 + g["cell_h"]
            page.draw_rect(fitz.Rect(x0, y0, x1, y1),
                           color=(0.80, 0.80, 0.80), width=0.55)
            # 十字辅助线（很浅，阈值切字时会被滤掉）
            page.draw_line((x0 + (x1 - x0) / 2, y0),
                           (x0 + (x1 - x0) / 2, y1),
                           color=(0.94, 0.94, 0.94), width=0.35)
            page.draw_line((x0, y0 + (y1 - y0) / 2),
                           (x1, y0 + (y1 - y0) / 2),
                           color=(0.94, 0.94, 0.94), width=0.35)
            fs = min(x1 - x0, y1 - y0) * 0.52
            if show_ref:
                try:
                    w = fitz.get_text_length(ch, fontname="china-s", fontsize=fs)
                except Exception:
                    w = fs
                page.insert_text((x0 + (x1 - x0 - w) / 2,
                                  y0 + (y1 - y0) / 2 + fs * 0.34),
                                 ch, fontname="china-s", fontsize=fs,
                                 color=(0.87, 0.87, 0.87))
            if show_idx:
                page.insert_text((x0 + 2, y0 + 7.5),
                                 str(page_info["start"] + i + 1),
                                 fontname="helv", fontsize=5.2,
                                 color=(0.76, 0.76, 0.76))
        if progress:
            progress(pi + 1, len(pages))
    doc.save(out_pdf, deflate=True, garbage=3)
    doc.close()
    return out_pdf


def save_bundle(chars, out_dir, passes=DEFAULT_PASSES,
                cell_pt=DEFAULT_CELL_PT, title="手写模板", stem="模板",
                show_reference=True, show_index=True, progress=None,
                write_pdf=True):
    """生成「PDF + layout.json + 字表 txt/csv」一整套，返回文件路径 dict。"""
    from . import charsets
    chars = charsets.normalize(chars)
    os.makedirs(out_dir, exist_ok=True)
    layout = make_layout(chars, passes=passes, cell_pt=cell_pt, title=title,
                         show_reference=show_reference, show_index=show_index)
    pdf = os.path.join(out_dir, stem + ".pdf")
    if write_pdf:
        build_pdf(layout, pdf, progress=progress)
    layout_path = os.path.join(out_dir, "layout.json")
    with open(layout_path, "w", encoding="utf-8") as fp:
        # pages 里的 chars 与顶层 chars 重复，存盘时去掉，文件小一半
        slim = dict(layout)
        slim["pages"] = [{k: v for k, v in p.items() if k != "chars"}
                         for p in layout["pages"]]
        json.dump(slim, fp, ensure_ascii=False, indent=2)
    chars_txt = os.path.join(out_dir, "chars.txt")
    charsets.save_list(chars, chars_txt)
    return {"pdf": pdf if write_pdf else "", "layout": layout_path,
            "chars_txt": chars_txt,
            "chars_csv": os.path.splitext(chars_txt)[0] + ".csv",
            "layout_data": layout}


def load_layout(path):
    """读 layout.json；把 pages 里缺的 chars 从顶层补回去。"""
    with open(path, encoding="utf-8") as fp:
        layout = json.load(fp)
    chars = layout.get("chars") or []
    for p in layout.get("pages") or []:
        if "chars" not in p:
            p["chars"] = chars[p.get("start", 0):p.get("end", 0)]
    return layout


def preview_png(layout, out_png, dpi=110, page_index=0):
    """把某页渲染成 PNG（界面里看版式）。"""
    import tempfile

    import fitz
    pages = layout.get("pages") or []
    if not pages:
        return ""
    page_index = max(0, min(page_index, len(pages) - 1))
    # 只画这一页：临时构造单页 layout
    one = dict(layout)
    one["pages"] = [pages[page_index]]
    one["pages_per_pass"] = 1
    tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    tmp.close()
    try:
        build_pdf(one, tmp.name)
        doc = fitz.open(tmp.name)
        pix = doc[0].get_pixmap(dpi=dpi)
        pix.save(out_png)
        doc.close()
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
    return out_png


def estimate(chars, cell_pt=DEFAULT_CELL_PT, passes=DEFAULT_PASSES):
    """预览「要写多少页」。"""
    n = len(chars)
    g = geometry(cell_pt)
    per = g["per_page"]
    pages = max(1, int(math.ceil(n / float(per)))) if n else 0
    return {"count": n, "cell_pt": round(g["cell_pt"], 1),
            "cols": g["cols"], "rows": g["rows"], "per_page": per,
            "pages_per_pass": pages, "passes": passes,
            "total_pages": pages * passes}


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from fontlib import charsets
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 330
    chars = charsets.gb2312_all()[:n]
    print(estimate(chars))
    out = os.path.join("out", "fontlab", "template_demo")
    files = save_bundle(chars, out, title="自检模板")
    print(files["pdf"], os.path.getsize(files["pdf"]))
