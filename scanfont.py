# -*- coding: utf-8 -*-
"""scanfont.py — 把纸质手写扫描件做成多套 TTF 字库（同字多形）。

两种稿件都能吃
--------------
A 模板稿（字序已知，全自动）
    python scanfont.py template
    → 打印 4 页，用深色笔写满，扫描成 pass1.jpg ... pass4.jpg
    python scanfont.py segment --grid --images out/scanfont/pass*.jpg
    → 每个字块自动知道是哪个字，无需人工标注

B 自由稿（你平时写的笔记/作业，字序未知）
    python scanfont.py segment --images D:/扫描件/*.jpg
    → 自动切出字块 + 带编号的 contact_sheet.png
    → 由人（或会看图的助手）填 labels.json：{"p1_000": "三", ...}
    python scanfont.py build --labels out/scanfont/labels.json

两者共用 build：把「已标注字块」按字符分组，同一个字攒到几个样本就得到几种形态，
分配到 N 套 TTF。缺字不强行补齐 —— 交给现有 Hand 的缺字回退，
multifont.py check 会报告每个字实际能拿到几种形态。

输出是标准 glyf TTF（不是 CFF），可直接再跑 merge_symbols.py 补数学符号。

依赖：numpy / Pillow / fontTools / PyMuPDF（仓库 requirements 已全覆盖，无需新增）。
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import re
import sys
from collections import defaultdict

import numpy as np
import fitz
from PIL import Image, ImageDraw, ImageFont, ImageOps
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, "out", "scanfont")

# ---------------------------------------------------------------- 版面常量
PAGE_W, PAGE_H = 595.0, 842.0          # A4, pt
MARGIN, MARK = 22.0, 14.0             # 四角黑方块
GRID_LEFT, GRID_TOP = 42.0, 78.0
GRID_RIGHT, GRID_BOTTOM = 553.0, 800.0
COLS = 8
PAD = 1.5                            # 切块时内缩一点点；格线是浅灰，靠阈值就能滤掉，
                                      # 留太大反而会把写满格的笔画裁掉

# ---------------------------------------------------------------- 字体度量
UPM = 1000
TARGET_H = 700                        # 归一化后字形墨迹高度（font units）
MAX_W = 880
BASELINE = 100                        # 墨迹底部距基线

# ---------------------------------------------------------------- 字表
DEFAULT_TEXT = None                   # 从 demo.TEXT 取，见 lazy_demo_text()


def lazy_demo_text():
    import demo
    return demo.TEXT


def load_text(args):
    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            return f.read()
    if args.text:
        return args.text
    return lazy_demo_text()


def charset_from_text(text):
    """正文去重字 + 公式里「真正显示出来」的拉丁/数字/运算符。

    LaTeX 命令名（\\frac \\sqrt \\mathrm ...）不是要手写的字符，先剥掉；
    控制符 {}$^_\\, 空白也剔除。剩下的字母数字才是公式里会看到的。
    """
    import multifont
    cn = multifont.text_chars(text)
    formulas = re.findall(r"\$([^$]*)\$", text)
    vis = []
    for f in formulas:
        c = re.sub(r"\\[a-zA-Z]+", "", f)
        c = re.sub(r"[{}$^_\\,\s]", "", c)
        vis.extend(c)
    extra = list(dict.fromkeys(
        c for c in vis if c.isalnum() or c in "=+-|.<>()×÷≈"))
    return cn + [c for c in extra if c not in cn]


def gname(cp):
    return "uni%04X" % cp if cp <= 0xFFFF else "u%05X" % cp


def _label_font(px):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


# ---------------------------------------------------------------- 图像工具
def _runs(mask1d):
    d = np.diff(np.concatenate(([0], mask1d.astype(np.int8), [0])))
    return list(zip(np.where(d == 1)[0].tolist(), np.where(d == -1)[0].tolist()))


def _neighbor_count(ink):
    p = np.pad(ink, 1, mode="constant")
    H, W = ink.shape
    n = np.zeros((H, W), dtype=np.int16)
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dy == 0 and dx == 0:
                continue
            n += p[1 + dy:1 + dy + H, 1 + dx:1 + dx + W]
    return n


def to_mask(pil_gray, threshold=128, denoise=True):
    """灰度图 → 墨迹 bool 掩膜。浅灰参考字/格线靠阈值滤掉，深墨笔迹保留。"""
    a = np.asarray(pil_gray.convert("L"), dtype=np.uint8)
    ink = a < threshold
    if denoise and ink.any():
        # 去掉孤立噪点：3x3 邻域里至少要有 1 个同伴
        ink = ink & (_neighbor_count(ink) >= 1)
        # 去掉贴边的一整条黑线（角标/装订线误入格内时）
        H, W = ink.shape
        if H > 8 and W > 8:
            ink[:1, :] = False
            ink[-1:, :] = False
            ink[:, :1] = False
            ink[:, -1:] = False
    return ink


def mask_ink_pixels(mask):
    return int(mask.sum())


def save_mask(mask, path):
    Image.fromarray((mask * 255).astype(np.uint8), mode="L").save(path)


def load_mask(path):
    return np.asarray(Image.open(path).convert("L"), dtype=np.uint8) > 127


# ---------------------------------------------------------------- 矢量化
def global_scale(mask_paths):
    """所有字块共用一把尺子。

    模板稿里每个字都写在同样大的格子里，自由稿里则按同一行高切分——
    因此不能每个字各自按 bbox 放大到 700 高：那样 '-' '.' '1' 这种墨迹很小的
    字符会被吹成大黑方块。用全部字块尺寸的中位数定一个全局缩放系数。

    注意必须量「墨迹包围盒」而不是「掩膜画布」：网格模式下每块都是整格
    （含大片空白），用画布高度算会把所有字缩到格子的 1/3，笔画细到看不清。
    """
    hs, ws = [], []
    for p in mask_paths:
        m = load_mask(p)
        ys, xs = np.where(m)
        if len(ys) == 0:
            continue
        hs.append(int(ys.max() - ys.min() + 1))
        ws.append(int(xs.max() - xs.min() + 1))
    if not hs:
        return 1.0
    return min(TARGET_H / float(np.median(hs)), MAX_W / float(np.median(ws)))


def mask_to_glyph(mask, s_global):
    """二值掩膜 → TrueType 字形。

    不做轮廓追踪（那需要 potrace/cv2），改用「行条带矩形」：
    每一行的连续墨迹段是一个矩形轮廓，上下宽度相同的相邻行合并成一条。
    全部轮廓同向，非零环绕填充后就是实心笔迹，天然支持笔画里的孔洞。
    零额外依赖，且生成的 glyf 可被 merge_symbols.py 继续写入符号。

    s_global 来自 global_scale()：所有字共用尺度，保留字与字的真实大小差异；
    墨迹水平居中、底边贴基线，不改变字符本身的高矮胖瘦。
    """
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return TTGlyphPen(None).glyph(), 0
    H, W = mask.shape
    # 兜底：个别字块异常大时单独收一点，避免撑出 em box
    s = min(s_global, TARGET_H / float(ys.max() - ys.min() + 1),
            MAX_W / float(xs.max() - xs.min() + 1))
    ink_w = (xs.max() - xs.min() + 1) * s
    x_off = (UPM - ink_w) / 2.0 - xs.min() * s
    y_top = BASELINE + (ys.max() + 1) * s

    pen = TTGlyphPen(None)
    active = {}                       # (x0, x1) -> [r0, r1]

    def emit(key, r0, r1):
        x0, x1 = key
        X0, X1 = x_off + x0 * s, x_off + x1 * s
        Y0, Y1 = y_top - r1 * s, y_top - r0 * s
        pen.moveTo((X0, Y0))
        pen.lineTo((X1, Y0))
        pen.lineTo((X1, Y1))
        pen.lineTo((X0, Y1))
        pen.closePath()

    for r in range(ys.min(), ys.max() + 1):
        row = mask[r]
        d = np.diff(np.concatenate(([0], row.astype(np.int8), [0])))
        starts = np.where(d == 1)[0]
        ends = np.where(d == -1)[0]
        runs = set(zip(starts.tolist(), ends.tolist()))
        for key in list(active):
            if key not in runs or active[key][1] != r - 1:
                emit(key, active[key][0], active[key][1] + 1)
                del active[key]
        for key in runs:
            if key in active:
                active[key][1] = r
            else:
                active[key] = [r, r]
    for key, (r0, r1) in active.items():
        emit(key, r0, r1 + 1)
    return pen.glyph(), mask_ink_pixels(mask)


def build_ttf(path, char_to_mask_path, family, ps_name):
    """用 FontBuilder 组装一份标准 glyf TTF。"""
    s_global = global_scale(list(char_to_mask_path.values()))
    cmap, glyphs, ink_stats = {}, {}, {}
    for ch, p in sorted(char_to_mask_path.items(), key=lambda kv: ord(kv[0])):
        n = gname(ord(ch))
        g, ink = mask_to_glyph(load_mask(p), s_global)
        glyphs[n] = g
        cmap[ord(ch)] = n
        ink_stats[ch] = ink

    order = [".notdef"] + [gname(ord(c)) for c in sorted(char_to_mask_path)]
    glyph_set = {".notdef": TTGlyphPen(None).glyph()}
    glyph_set.update(glyphs)

    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyph_set)
    fb.setupHorizontalMetrics({g: (UPM, 0) for g in order})
    fb.setupHorizontalHeader(ascent=800, descent=-200, lineGap=0)
    fb.setupNameTable({
        1: family, 2: "Regular", 3: "%s-%s" % (family, ps_name),
        4: "%s Regular" % family, 5: "Version 1.0", 6: ps_name,
    })
    fb.setupOS2(sTypoAscender=800, sTypoDescender=-200,
                usWinAscent=800, usWinDescent=200, usWeightClass=400)
    fb.setupPost()
    fb.setupMaxp()
    fb.save(path)
    return ink_stats


# ---------------------------------------------------------------- template
# 英文字母 + 常见物理/数学符号字表（按类分组，顺序即模板格子顺序）
# 说明：
#   · 希腊字母取物理公式高频集（α–ω 全留，大小写都收）
#   · 减号用 U+2212（数学减号）而非 ASCII '-'，与 merge_symbols 的符号表一致
#   · 上标 ² ³ 不收：scanfont.build 会把每个字形按墨迹高度归一化到 700 units，
#     手写的小号 2/3 会被拉成全尺寸，上标语义反而丢了。上标交给公式层缩放。
EN_GROUPS = [
    ("大写 A-Z", "ABCDEFGHIJKLMNOPQRSTUVWXYZ"),
    ("小写 a-z", "abcdefghijklmnopqrstuvwxyz"),
    ("数字 0-9", "0123456789"),
    ("希腊小写", "αβγδεζηθικλμνξοπρστυφχψω"),
    ("希腊大写", "ΓΔΘΛΞΠΣΥΦΨΩ"),
    ("运算符", "+−×÷±=≠≈≤≥∝"),
    ("微积分", "∑∫∏∂∇√∞"),
    ("几何角", "°∠⊥∥"),
    ("集合箭头", "∈⊂∪∩→"),
    ("括号件", "⟨⟩"),
]
EN_CHARSET = "".join(g[1] for g in EN_GROUPS)


def pick_ref_font(explicit=None):
    """参考字字体：必须能画希腊字母和数学符号，否则模板上会是豆腐块。

    只用 Segoe UI Symbol（实测 Ω√≈∑∫∂∇αβγ≤≥∠⊥ 全覆盖）；跨平台退 DejaVu Sans。
    Segoe 是微软专有字体，仅嵌入本地打印用的 PDF，不随仓库分发。
    """
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for p in (r"C:\Windows\Fonts\seguisym.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
              r"C:\Windows\Fonts\arial.ttf"):
        if os.path.exists(p):
            return p
    return None


def _legend_lines(groups, fontsize, x0, x1):
    """把「分类名(数量)」排成若干行，返回 [(seg, x), ...] 的行列表。

    网格起点要按行数下移，绘制时又要按同样的断行画，所以两处必须共用这一份计算。
    """
    lines, cur, gx = [], [], x0
    for name, s in groups:
        seg = "%s(%d)" % (name, len(s))
        w = fitz.get_text_length(seg + "  ", fontname="china-s", fontsize=fontsize)
        if cur and gx + w > x1:
            lines.append(cur)
            cur, gx = [], x0
        cur.append((seg, gx))
        gx += w
    if cur:
        lines.append(cur)
    return lines


def cmd_template(args):
    # 字表来源：--chars 自定义 > --charset en 内置英文/符号表 > 默认从正文提取
    groups = None
    if getattr(args, "chars", None):
        chars = [c for c in dict.fromkeys(args.chars) if not c.isspace()]
    elif getattr(args, "charset", "default") == "en":
        chars = list(dict.fromkeys(EN_CHARSET))
        groups = EN_GROUPS
    else:
        chars = charset_from_text(load_text(args))

    cols = getattr(args, "cols", COLS) or COLS

    # 图例占几行会影响网格起点，先算出来，绘制时按同一份断行画
    lf = 6.8
    legend = _legend_lines(groups, lf, GRID_LEFT, GRID_RIGHT) if groups else []
    grid_top = GRID_TOP
    if legend:
        grid_top = 74 + (len(legend) - 1) * 8.5 + 14

    rows = int(math.ceil(len(chars) / float(cols)))
    cw = (GRID_RIGHT - GRID_LEFT) / cols
    ch = (GRID_BOTTOM - grid_top) / rows
    os.makedirs(args.out, exist_ok=True)

    ref_path = pick_ref_font(getattr(args, "ref_font", None))
    if ref_path is None and groups is not None:
        print("WARN: 没找到符号字体，参考字可能显示为空白（不影响格子）")

    doc = fitz.open()
    for p in range(1, args.passes + 1):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_text((GRID_LEFT, 50),
                         "第 %d 遍 / Pass %d    共 %d 字" % (p, p, len(chars)),
                         fontname="china-s", fontsize=11, color=(0.1, 0.1, 0.1))
        page.insert_text((GRID_LEFT, 64),
                         "每格写一个，写满格、居中；浅灰是参考字，不用描它。空格会变成缺字。",
                         fontname="china-s", fontsize=7.5, color=(0.45, 0.45, 0.45))
        if legend:
            # 分类图例：告诉用户格子按什么顺序排，写的时候不用来回翻 chars.txt
            for li, line in enumerate(legend):
                for seg, gx in line:
                    page.insert_text((gx, 74 + li * 8.5), seg, fontname="china-s",
                                     fontsize=lf, color=(0.5, 0.5, 0.55))

        for (x, y) in ((MARGIN, MARGIN), (PAGE_W - MARGIN - MARK, MARGIN),
                       (MARGIN, PAGE_H - MARGIN - MARK),
                       (PAGE_W - MARGIN - MARK, PAGE_H - MARGIN - MARK)):
            page.draw_rect(fitz.Rect(x, y, x + MARK, y + MARK),
                           color=None, fill=(0, 0, 0))
        # 每页只注册一次字体：放在逐字循环里会让 PDF 把整个字体重复嵌入上百次
        rf = None
        if ref_path:
            page.insert_font(fontname="ref", fontfile=ref_path)
            rf = fitz.Font(fontfile=ref_path)

        for i, c in enumerate(chars):
            col, row = i % cols, i // cols
            x0, y0 = GRID_LEFT + col * cw, grid_top + row * ch
            page.draw_rect(fitz.Rect(x0, y0, x0 + cw, y0 + ch),
                           color=(0.82, 0.82, 0.82), width=0.6)
            page.draw_line((x0 + cw / 2, y0), (x0 + cw / 2, y0 + ch),
                           color=(0.93, 0.93, 0.93), width=0.4)
            page.draw_line((x0, y0 + ch / 2), (x0 + cw, y0 + ch / 2),
                           color=(0.93, 0.93, 0.93), width=0.4)
            fs = min(cw, ch) * 0.42
            if rf is not None:
                # 嵌入字体：希腊字母/数学符号才画得出来，且能用真实字宽度量居中
                w = rf.text_length(c, fontsize=fs)
                base = y0 + ch / 2 + (rf.ascender + rf.descender) / 2 * fs
                page.insert_text((x0 + (cw - w) / 2, base), c,
                                 fontname="ref", fontsize=fs,
                                 color=(0.87, 0.87, 0.87))
            else:
                try:
                    w = fitz.get_text_length(c, fontname="china-s", fontsize=fs)
                except Exception:
                    w = fs
                page.insert_text((x0 + (cw - w) / 2, y0 + ch / 2 + fs * 0.35),
                                 c, fontname="china-s", fontsize=fs,
                                 color=(0.87, 0.87, 0.87))
            page.insert_text((x0 + 2, y0 + 8), str(i + 1),
                             fontname="helv", fontsize=5.5,
                             color=(0.72, 0.72, 0.72))
        page.insert_text((GRID_LEFT, PAGE_H - 16),
                         "pdf-layer scanfont  |  pass %d  |  请勿裁掉四角黑块" % p,
                         fontname="helv", fontsize=7, color=(0.5, 0.5, 0.5))

    pdf = os.path.join(args.out, "scan_template.pdf")
    # 参考字字体（Segoe UI Symbol 约 2.4MB）整份嵌入会让 PDF 涨到 1.7MB；
    # 子集化只保留实际用到的百来个字形，压到 0.2MB 左右，打印效果不变。
    if ref_path:
        try:
            doc.subset_fonts()
        except Exception:
            pass
    doc.save(pdf, deflate=True, garbage=4)
    if getattr(args, "preview", False):
        doc[0].get_pixmap(dpi=140).save(os.path.join(args.out, "template_preview.png"))
    doc.close()

    layout = {
        "page": {"w": PAGE_W, "h": PAGE_H, "dpi": args.dpi},
        "grid": {"left": GRID_LEFT, "top": grid_top, "cols": cols,
                 "rows": rows, "cell_w": cw, "cell_h": ch, "pad": PAD},
        "markers": {"margin": MARGIN, "size": MARK},
        "chars": chars, "count": len(chars), "passes": args.passes,
    }
    if groups:
        layout["groups"] = [{"name": n, "count": len(s)} for n, s in groups]
    with open(os.path.join(args.out, "layout.json"), "w", encoding="utf-8") as f:
        json.dump(layout, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "chars.txt"), "w", encoding="utf-8") as f:
        if groups:
            for n, s in groups:
                f.write("# %s（%d 字）\n%s\n\n" % (n, len(s), s))
            f.write("\n")
        for i in range(0, len(chars), 20):
            f.write("".join(chars[i:i + 20]) + "\n")
        f.write("\n")
        for i, c in enumerate(chars, 1):
            f.write("%3d  %s  U+%04X\n" % (i, c, ord(c)))

    print("template -> %s  (%d pages, %d chars, %d cols x %d rows)" %
          (pdf, args.passes, len(chars), cols, rows))
    print("glyphs   -> %d x %d pt each" % (round(cw, 1), round(ch, 1)))
    print("ref font -> %s" % (ref_path or "china-s (无符号覆盖风险)"))
    print("layout   -> %s/layout.json" % args.out)
    print("chars    -> %s/chars.txt" % args.out)
    return 0


# ---------------------------------------------------------------- 角标校正
def find_markers(gray):
    a = np.asarray(gray, dtype=np.uint8)
    H, W = a.shape
    k = W / PAGE_W
    theory = [
        (MARGIN + MARK / 2, MARGIN + MARK / 2),
        (PAGE_W - MARGIN - MARK / 2, MARGIN + MARK / 2),
        (MARGIN + MARK / 2, PAGE_H - MARGIN - MARK / 2),
        (PAGE_W - MARGIN - MARK / 2, PAGE_H - MARGIN - MARK / 2),
    ]
    centers = []
    for (mx, my) in theory:
        cx, cy = mx * k, my * k
        win = int(24 * k)
        x0, y0 = max(0, int(cx - win)), max(0, int(cy - win))
        x1, y1 = min(W, int(cx + win)), min(H, int(cy + win))
        sub = a[y0:y1, x0:x1]
        ys, xs = np.where(sub < 80)
        if len(xs) < 20:
            return None
        centers.append((float(np.median(xs) + x0), float(np.median(ys) + y0)))
    # 偏离理论位置太远视为检测失败
    for (cx, cy), (tx, ty) in zip(centers, theory):
        if abs(cx - tx * k) > 30 * k or abs(cy - ty * k) > 30 * k:
            return None
    return centers


def align_markers(gray, centers):
    """用四个角标中心做「缩放 + 平移」对齐到标准页面坐标。

    注意：角标中心是内缩在页面里的（距边 m pt），不是页面四角。
    早期版本把角标中心当作 QUAD 四角映射到整页，会把整页放大约 11%，
    导致按理论网格切块时字被裁掉半边。这里只求 scale/offset，
    扫描件若本来就是正的，变换恒等，不做任何重采样损伤。
    """
    k = gray.width / PAGE_W
    m = MARGIN + MARK / 2
    tl, tr, bl, br = centers
    sx = (tr[0] - tl[0]) / ((PAGE_W - 2 * m) * k)
    sy = (bl[1] - tl[1]) / ((PAGE_H - 2 * m) * k)
    ox = tl[0] - m * k * sx
    oy = tl[1] - m * k * sy
    if not (0.7 < sx < 1.4 and 0.7 < sy < 1.4):
        return gray
    W, H = int(PAGE_W * k), int(PAGE_H * k)
    # PIL AFFINE 用的是「输出坐标 -> 源坐标」的逆映射
    coeffs = (1.0 / sx, 0.0, -ox / sx, 0.0, 1.0 / sy, -oy / sy)
    return gray.transform((W, H), Image.Transform.AFFINE, coeffs,
                          resample=Image.Resampling.BICUBIC)


# ---------------------------------------------------------------- segment
def cmd_segment(args):
    os.makedirs(args.out, exist_ok=True)
    images = sorted(sum([glob.glob(p) for p in args.images], []))
    if not images:
        print("NO IMAGES matched:", args.images)
        return 1
    labels, boxes_all = {}, {}

    if args.grid:
        with open(args.layout, "r", encoding="utf-8") as f:
            layout = json.load(f)
        g = layout["grid"]
        chars = layout["chars"]
        for pi, ipath in enumerate(images, 1):
            gray = Image.open(ipath).convert("L")
            if not args.no_warp:
                c = find_markers(gray)
                if c:
                    gray = align_markers(gray, c)
                    print("  pass%d: 角标对齐 OK" % pi)
                else:
                    print("  pass%d: 未找到四角标记，按理论网格切（扫描件通常无透视）" % pi)
            k = gray.width / PAGE_W
            for i, ch in enumerate(chars):
                col, row = i % g["cols"], i // g["cols"]
                x0 = (g["left"] + col * g["cell_w"] + PAD) * k
                y0 = (g["top"] + row * g["cell_h"] + PAD) * k
                x1 = (g["left"] + (col + 1) * g["cell_w"] - PAD) * k
                y1 = (g["top"] + (row + 1) * g["cell_h"] - PAD) * k
                crop = gray.crop((int(x0), int(y0), int(x1), int(y1)))
                m = to_mask(crop, args.threshold)
                # 空格子（用户跳过的字）不写进 labels：否则会生成一个没有轮廓的
                # 字形，渲染时该字变成空白，比缺字回退更难查。
                if mask_ink_pixels(m) < args.min_ink:
                    print("  pass%d: 第 %d 格（%s）几乎无墨迹，跳过" % (pi, i + 1, ch))
                    continue
                name = "p%d_%03d" % (pi, i)
                save_mask(m, os.path.join(args.out, name + ".png"))
                labels[name] = ch
            print("  pass%d: 切出 %d 块 <- %s" %
                  (pi, sum(1 for k in labels if k.startswith("p%d_" % pi)),
                   os.path.basename(ipath)))
    else:
        # 自由稿：投影法切行 + 按估计字宽找谷点切字；边界可手工改 boxes.json 后再切
        for pi, ipath in enumerate(images, 1):
            gray = Image.open(ipath).convert("L")
            m = to_mask(gray, args.threshold, denoise=True)
            H, W = m.shape
            rowsum = m.sum(axis=1)
            bands = [(a, b) for a, b in _runs(rowsum > max(3, int(W * 0.008)))
                     if b - a > H * 0.012]
            boxes = []
            for (y0, y1) in bands:
                hh = y1 - y0
                colsum = m[y0:y1].sum(axis=0).astype(float)
                win = max(3, int(hh * 0.10))
                ker = np.ones(win) / win
                sm = np.convolve(np.pad(colsum, (win, win), mode="constant"),
                                 ker, mode="same")[win:-win]
                est = hh * 0.95
                x = 0
                while x < W - est * 0.5:
                    lo, hi = int(x + est * 0.55), int(min(W - 1, x + est * 1.45))
                    if hi <= lo:
                        break
                    cut = lo + int(np.argmin(sm[lo:hi]))
                    boxes.append((x, y0, min(W, cut), y1))
                    x = cut + 1
            for bi, (x0, y0, x1, y1) in enumerate(boxes):
                pad = 2
                crop = gray.crop((max(0, x0 - pad), max(0, y0 - pad),
                                  min(W, x1 + pad), min(H, y1 + pad)))
                cm = to_mask(crop, args.threshold)
                if mask_ink_pixels(cm) < args.min_ink:
                    continue
                name = "p%d_%03d" % (pi, bi)
                save_mask(cm, os.path.join(args.out, name + ".png"))
                boxes_all[name] = [x0, y0, x1, y1]
            print("  page%d: 切出 %d 块 <- %s" %
                  (pi, sum(1 for k in boxes_all if k.startswith("p%d_" % pi)),
                   os.path.basename(ipath)))
        with open(os.path.join(args.out, "boxes.json"), "w", encoding="utf-8") as f:
            json.dump(boxes_all, f, ensure_ascii=False, indent=2)

    with open(os.path.join(args.out, "labels.json"), "w", encoding="utf-8") as f:
        json.dump(labels, f, ensure_ascii=False, indent=2)
    print("labels   -> %s/labels.json  (%d 条%s)" %
          (args.out, len(labels), "，网格模式已自动填好" if args.grid else "，需人工/视觉标注"))

    # 带编号的联系表：认字就看这张
    keys = sorted(labels) if args.grid else sorted(boxes_all)
    if keys:
        cell, lab = 76, 18
        cols = 12
        rows = int(math.ceil(len(keys) / float(cols)))
        sheet = Image.new("RGB", (cols * cell, rows * (cell + lab)), "white")
        d = ImageDraw.Draw(sheet)
        lf = _label_font(11)
        for i, key in enumerate(keys):
            c, r = i % cols, i // cols
            x, y = c * cell, r * (cell + lab)
            p = os.path.join(args.out, key + ".png")
            if not os.path.exists(p):
                continue
            tile = Image.open(p).convert("L")
            tile = ImageOps.invert(tile)          # 白底黑字，认字更自然
            tile.thumbnail((cell - 8, cell - 8))
            sheet.paste(Image.merge("RGB", (tile, tile, tile)),
                        (x + (cell - tile.width) // 2, y + lab))
            d.text((x + 3, y + 2), key + (" " + labels[key] if key in labels else ""),
                   fill=(20, 20, 20), font=lf)
        sheet.save(os.path.join(args.out, "contact_sheet.png"))
        print("sheet    -> %s/contact_sheet.png" % args.out)
    return 0


# ---------------------------------------------------------------- build
def cmd_build(args):
    with open(args.labels, "r", encoding="utf-8") as f:
        labels = json.load(f)
    blocks_dir = args.blocks or os.path.dirname(os.path.abspath(args.labels))
    by_char = defaultdict(list)
    for name, ch in labels.items():
        if not ch:
            continue
        p = os.path.join(blocks_dir, name + ".png")
        if os.path.exists(p):
            by_char[ch].append(p)
    if not by_char:
        print("NO labeled blocks in", blocks_dir)
        return 1
    os.makedirs(args.out, exist_ok=True)

    target = set(charset_from_text(load_text(args)))
    report = {"sets": [], "chars": {}, "missing_vs_target": sorted(target - set(by_char))}
    for k in range(1, args.sets + 1):
        chosen, reused = {}, []
        for ch, files in by_char.items():
            idx = (k - 1) % len(files)
            chosen[ch] = files[idx]
            if len(files) < args.sets:
                reused.append(ch)
        path = os.path.join(args.out, "pass%d.ttf" % k)
        fam = "%s%d" % (args.family, k)
        stats = build_ttf(path, chosen, fam, fam.replace("-", ""))
        report["sets"].append({"pass": k, "file": os.path.basename(path),
                               "chars": len(chosen),
                               "ink_min": min(stats.values()) if stats else 0,
                               "reused_chars": sorted(set(reused))})
        print("pass%d -> %s  (%d chars)" % (k, path, len(chosen)))
    for ch, files in sorted(by_char.items(), key=lambda kv: ord(kv[0])):
        report["chars"][ch] = {"samples": len(files),
                               "forms": min(len(files), args.sets)}
    with open(os.path.join(args.out, "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    n_full = sum(1 for v in report["chars"].values() if v["forms"] >= args.sets)
    print()
    print("形态统计: %d 字有 >=%d 种形态, %d 字样本不足(将跨套复用)" %
          (n_full, args.sets, len(report["chars"]) - n_full))
    if report["missing_vs_target"]:
        print("目标字表未覆盖: %d 字 -> %s" %
              (len(report["missing_vs_target"]), "".join(report["missing_vs_target"])))
        print("  (这些字交给 Hand 的缺字回退，不会出豆腐块)")
    print("report   -> %s/build_report.json" % args.out)

    # 预览：4 套 × 前 N 字
    if args.preview:
        cjk = [c for c in sorted(by_char) if not c.isascii()]
        picks = (cjk + [c for c in sorted(by_char) if c.isascii()])[:args.preview_chars]
        cell, lab = 72, 20
        img = Image.new("RGB", (cell * (len(picks) + 1),
                                (args.sets + 1) * (cell + lab)), "white")
        d = ImageDraw.Draw(img)
        lf = _label_font(12)
        for c, ch in enumerate(picks, 1):
            d.text((c * cell + 4, 4), "%s U+%04X" % (ch, ord(ch)),
                   fill=(60, 60, 60), font=lf)
        for r in range(1, args.sets + 1):
            y = r * (cell + lab)
            d.text((4, y + 4), "pass%d" % r, fill=(60, 60, 60), font=lf)
            try:
                f = ImageFont.truetype(os.path.join(args.out, "pass%d.ttf" % r), 46)
            except Exception:
                continue
            for c, ch in enumerate(picks, 1):
                d.text((c * cell + 8, y + lab), ch, font=f, fill=(16, 16, 18))
        p = os.path.join(args.out, "preview.png")
        img.save(p)
        print("preview  -> %s" % p)
    return 0


# ---------------------------------------------------------------- selftest
def cmd_selftest(args):
    """合成 4 页「扫描件」跑通全链路（用 4 款现成字体模拟 4 遍不同笔迹）。"""
    import io
    os.makedirs(args.out, exist_ok=True)
    # 1. template
    t = argparse.Namespace(text=None, text_file=None, out=args.out,
                           passes=4, dpi=args.dpi)
    cmd_template(t)
    with open(os.path.join(args.out, "layout.json"), "r", encoding="utf-8") as f:
        layout = json.load(f)
    g, chars = layout["grid"], layout["chars"]
    k = args.dpi / 72.0
    W, H = int(PAGE_W * k), int(PAGE_H * k)
    fallback = "C:/Windows/Fonts/msyh.ttc"
    # 用 4 款「规整可辨」的字体模拟 4 遍手写；草书体（刘建毛草/龙藏）会让
    # 自测预览难以辨认，但那只影响演示观感，不影响脚本链路的正确性。
    fonts = [os.path.join(HERE, "fonts", n) for n in (
        "MaShanZheng-Regular-merged.ttf", "ZhiMangXing-Regular-merged.ttf")]
    fonts += ["C:/Windows/Fonts/simkai.ttf", "C:/Windows/Fonts/simhei.ttf"]
    fonts = [p for p in fonts if os.path.exists(p)] or [fallback]
    gray_ref = int(255 * 0.87)

    for p in range(1, 5):
        img = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(img)
        # 角标
        for (mx, my) in ((MARGIN, MARGIN), (PAGE_W - MARGIN - MARK, MARGIN),
                         (MARGIN, PAGE_H - MARGIN - MARK),
                         (PAGE_W - MARGIN - MARK, PAGE_H - MARGIN - MARK)):
            d.rectangle((mx * k, my * k, (mx + MARK) * k, (my + MARK) * k), fill=0)
        # 浅灰格线 + 参考字
        for i, ch in enumerate(chars):
            col, row = i % g["cols"], i // g["cols"]
            x0, y0 = (g["left"] + col * g["cell_w"]) * k, (g["top"] + row * g["cell_h"]) * k
            x1, y1 = x0 + g["cell_w"] * k, y0 + g["cell_h"] * k
            d.rectangle((x0, y0, x1, y1), outline=int(255 * 0.82), width=1)
            d.line((x0, (y0 + y1) / 2, x1, (y0 + y1) / 2), fill=int(255 * 0.93))
        # 深墨「手写字」：用该套字体
        fp = fonts[(p - 1) % len(fonts)]
        fs = int(min(g["cell_w"], g["cell_h"]) * k * 0.55)
        try:
            f = ImageFont.truetype(fp, fs)
            ff = ImageFont.truetype(fallback, fs)
        except Exception:
            f = ff = ImageFont.load_default()
        cmap_fp = set(TTFont(fp).getBestCmap() or {}) if os.path.exists(fp) else set()
        cwpx, chpx = int(g["cell_w"] * k), int(g["cell_h"] * k)
        for i, ch in enumerate(chars):
            col, row = i % g["cols"], i // g["cols"]
            x0 = int((g["left"] + col * g["cell_w"]) * k)
            y0 = int((g["top"] + row * g["cell_h"]) * k)
            use = f if ord(ch) in cmap_fp else ff
            # 每格单独渲染再贴回：字绝不会溢出到相邻格，切块才干净
            tile = Image.new("L", (cwpx, chpx), 255)
            td = ImageDraw.Draw(tile)
            bb = td.textbbox((0, 0), ch, font=use)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            td.text(((cwpx - tw) / 2 - bb[0], (chpx - th) / 2 - bb[1]),
                    ch, font=use, fill=0)
            img.paste(tile, (x0, y0))
        ip = os.path.join(args.out, "pass%d.jpg" % p)
        img.convert("RGB").save(ip, quality=92)
        print("  synth -> %s" % ip)

    # 2. segment --grid
    s = argparse.Namespace(images=[os.path.join(args.out, "pass*.jpg")],
                           out=args.out, grid=True,
                           layout=os.path.join(args.out, "layout.json"),
                           threshold=128, no_warp=False, min_ink=20)
    print()
    cmd_segment(s)
    # 3. build
    b = argparse.Namespace(labels=os.path.join(args.out, "labels.json"),
                           blocks=args.out, out=os.path.join(args.out, "fonts"),
                           sets=4, family="ScanHand", text=None, text_file=None,
                           preview=True, preview_chars=12)
    print()
    cmd_build(b)
    print()
    print("selftest done. 接着可跑:")
    print("  python multifont.py check --fonts %s" % os.path.join(args.out, "fonts"))
    return 0


# ---------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(description="纸质手写扫描件 → 多套 TTF 字库（同字多形）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--text", help="正文（默认取 demo.TEXT）")
        p.add_argument("--text-file", help="从 UTF-8 文件读正文")

    p = sub.add_parser("template", help="生成 A4 打印模板（固定字表 + 角标）")
    common(p)
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--passes", type=int, default=4)
    p.add_argument("--dpi", type=int, default=200)
    p.add_argument("--charset", choices=["default", "en"], default="default",
                   help="default=从正文提取；en=英文字母 + 数字 + 希腊字母 + 物理符号")
    p.add_argument("--chars", help="直接给字表字符串（会去重，空格忽略）")
    p.add_argument("--cols", type=int, default=COLS, help="每行格数")
    p.add_argument("--ref-font", help="参考字字体（默认自动找 Segoe UI Symbol）")
    p.add_argument("--preview", action="store_true", help="额外导出第一页 PNG 预览")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("segment", help="切分扫描图 → 字块 + 联系表 + labels.json")
    p.add_argument("--images", nargs="+", required=True, help="扫描图，支持通配符")
    p.add_argument("--out", default=DEFAULT_OUT)
    p.add_argument("--grid", action="store_true", help="模板稿：按固定网格切，字序自动已知")
    p.add_argument("--layout", default=os.path.join(DEFAULT_OUT, "layout.json"))
    p.add_argument("--no-warp", action="store_true", help="跳过四角透视校正")
    p.add_argument("--threshold", type=int, default=128, help="灰度阈值，低于它算墨迹")
    p.add_argument("--min-ink", type=int, default=20, help="自由稿字块最少墨迹像素")
    p.set_defaults(func=cmd_segment)

    p = sub.add_parser("build", help="已标注字块 → N 套 glyf TTF")
    common(p)
    p.add_argument("--labels", default=os.path.join(DEFAULT_OUT, "labels.json"))
    p.add_argument("--blocks", help="字块目录（默认同 labels.json）")
    p.add_argument("--out", default=os.path.join(DEFAULT_OUT, "fonts"))
    p.add_argument("--sets", type=int, default=4)
    p.add_argument("--family", default="ScanHand")
    p.add_argument("--preview", action="store_true")
    p.add_argument("--preview-chars", type=int, default=12)
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("selftest", help="合成 4 页扫描件跑通全链路")
    p.add_argument("--out", default=os.path.join(DEFAULT_OUT, "selftest"))
    p.add_argument("--dpi", type=int, default=200)
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
