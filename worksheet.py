# -*- coding: utf-8 -*-
"""worksheet.py — 作业页 → 题目/答题区自动识别 → AI 答案手写排版（headless）。

定位
----
「做题」链路的数据层：不依赖 GUI / tkinter，也不弹任何窗口，只做三件事：

1. ``analyze(source, page=None) -> spec``
   导入作业页（数字版 PDF / 扫描件 / 照片），自动划定每题「题目区域」与
   「答题区域」，输出稳定结构的 JSON spec（headless CLI 与 HTTP API 共用）。
   · 数字版 PDF：``page.get_text("dict")`` 取文字行，按题号正则识别题目块
     （支持 ``4.2.1`` 多级编号、``1.`` / ``（1）`` / ``一、``）；题干被换行时
     用行距把续行并入同一题；答题区 = 题目下方到下一题/页底之间的空白，并把
     其中的横线识别为 ``answer_lines``；页面里的插图（图片块）也会并进题目区，
     答题区在插图前止步，不会把答案写在图上；
   · 图片/扫描件（以及没有文字层的 PDF 页）：numpy 行/列墨迹投影 +
     scipy.ndimage 连通域去噪；用「表头长横线 / 页脚居中页码」排除页眉页脚，
     用「首行缩进」判断题目起始行（换行续行并入同题）；没有文字层所以
     ``question_text=None``，但返回每题区域裁剪的 ``crop_png_base64`` 供 AI
     多模态读题。题目上下排布很紧时（如扫描书页：题干都在页顶、下面是整片
     空白），答题区按整页空白区从上到下顺序分给各题。
2. ``render_answers(spec, answers, ...)``
   把 ``{题号 或 id: 答案文本}`` 用 page_model + symbols.make_han_hand 排进
   各题 ``answer_rect``；字号默认 12.4pt（不从大往小试，也不因区域大而放大），
   只有答案超过答题区高度时才逐步缩到 8pt；仍放不下只给 warning 不截断；
   导出叠加到原 PDF（原内容保留）或 PNG 预览。
   ``answer_mode="blank"`` 时不写原页：每个作业页后插入空白答题页，答案按题
   顺序排版（带题号），一题太长自动续页——适合「只有题目、没有留白」的作业页；
3. ``load_spec / save_spec`` —— spec 的 JSON 读写。

坐标统一为 pt（1pt = 1/72 英寸），页面坐标原点在左上角。

spec 结构（字段稳定，CLI 的 layout.json 与 HTTP API 共用）
--------------------------------------------------------
    {"source": "...", "kind": "pdf|image", "version": "1.1",
     "pages": [
       {"page": 0, "width_pt": 595.0, "height_pt": 842.0,
        "questions": [
          {"id": "Q1", "number": "1", "text": "题目文字或 null",
           "rect": [l, t, r, b], "answer_rect": [l, t, r, b],
           "answer_lines": 3, "confidence": 0.82,
           "crop_png_base64": "..."   # 仅图片/扫描页有：题目区域裁剪 PNG
          }],
        "warnings": ["..."]}],
     "warnings": ["..."]}

``answer_lines`` 的语义：
· 数字版 PDF 且识别到答题横线 → 横线条数（照抄版面）；
· 扫描件/图片或没有横线 → 按答题区高度估算的可写行数
  （12.4pt 正文、行距 1.85 倍时 ≈ 每行 22.94pt）。

命令行用法见 ``做题.py``；HTTP 用法见 ``docs/做题接口.md``。
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import sys

SPEC_VERSION = "1.1"

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ======================================================================
# 可调参数（检测阈值集中在这里，注释说明各参数的物理含义）
# ======================================================================
# 题号正则：1. / 2． / 3、 / 4) / （5） / 一、 / 4.2.1 / 4.3.5 / 12.4.1
# 行首匹配；多级编号（4.2.1）与单级编号（1.）都要支持。
QUESTION_RE = re.compile(
    r"^\s*(?:"
    r"\d+(?:[.．]\d+){1,3}[.．、)]?"          # 多级编号 4.2.1 / 12.4.3
    r"|\d{1,2}[.．、)]"                      # 单级编号 1. / 2、 / 3)
    r"|[（(]\d{1,2}[）)]"                    # （1） / (2)
    r"|[一二三四五六七八九十]+[、.]"          # 一、 / 十二.
    r")")
MULTI_LEVEL_RE = re.compile(r"^\d+(?:[.．]\d+){1,3}$")
# 实际解析用的严格版（避免把行首小数「1.5倍」误判成题号）：
# · 多级编号后面必须是空白/分隔符/行尾；
# · 单级编号的「.」后面不能再跟数字。
_MULTI_NUM_RE = re.compile(
    r"^\s*(\d+(?:[.．]\d+){1,3})(?=[ \t\u3000、．.）)]|$)")
_SINGLE_NUM_RE = re.compile(r"^\s*(\d{1,2})[.．、)](?!\d)")
_PAREN_NUM_RE = re.compile(r"^\s*[（(](\d{1,2})[）)]")
_CN_NUM_RE = re.compile(r"^\s*([一二三四五六七八九十]+)[、.]")

# ---- 数字版 PDF 答题区
ANSWER_PAD_RATIO = 0.35        # 答题区与题目/下一题之间留白 = 中位行高 × 该比例
ANSWER_PAD_MIN_PT = 2.5
ANSWER_PAD_MAX_PT = 8.0
ANSWER_BOTTOM_MARGIN_MIN = 24.0   # 页底留白下限（pt）
ANSWER_BOTTOM_MARGIN_RATIO = 0.07 # 页底留白 = 页高 × 比例，夹在上下限之间
ANSWER_BOTTOM_MARGIN_MAX = 56.0
MIN_ANSWER_H_PT = 24.0            # 答题区高度低于此值视为「排布过紧」
MIN_ANSWER_W_PT = 60.0            # 答题区宽度下限（pt）
MIN_GRAY_LINE_LEN = 40.0          # 横线长度下限（pt），滤掉短下划线/表格碎线
GRAY_LINE_YDUP_PT = 1.2           # 同一横线的 y 抖动合并阈值（pt）
CONTINUATION_GAP_RATIO = 0.9      # 题内换行：行间距 ≤ 中位行高×比例 视为同一题
CONTINUATION_GAP_MIN_PT = 6.0     # 但至少容忍 6pt 的换行间距
MAX_QUESTIONS_PER_PAGE = 80
QUESTION_GROUP_GAP_RATIO = 8.0    # 扫描件：题目块内行间距上限 = 中位行高×比例
STACK_GAP_PT = 6.0                # 题目排布很紧时，各题答题区之间的间隔（pt）

# ---- 图片/扫描件模式（numpy 投影 + scipy.ndimage）
SCAN_DPI_DEFAULT = 200            # 图片没写 dpi 时按 200dpi（A4 扫描的常用值）
SCAN_PDF_DPI = 150                # 没有文字层的 PDF 页栅格化 dpi（裁剪图不超大）
SCAN_TEXT_THRESHOLD = 150         # 灰度 < 该值视为墨迹
SCAN_MIN_COMPONENT_PX = 12        # 连通域像素数下限：去掉扫描噪点
SCAN_ROW_THRESHOLD_RATIO = 0.002  # 行墨迹像素 ≥ 图宽×比例 才算文字行
SCAN_ROW_THRESHOLD_MIN = 4
SCAN_BAND_MERGE_GAP_PX = 2        # 同一文字行内断开的行间隙（px）
SCAN_BAND_MERGE_RATIO = 1.0       # 无缩进时：行间距 ≤ 中位行高×比例 → 同一题
SCAN_BLOCK_MERGE_MIN_PX = 6
SCAN_CROP_PAD_PX = 8              # 题目区域裁剪向外扩的像素
SCAN_MAX_QUESTIONS = 60

# 表头/页脚识别
IMG_HEADER_REGION = 0.15          # 页高顶部 15% 内才可能是表头
IMG_HEADER_RUN_RATIO = 0.06       # 表头特征：有一条 ≥ 图宽×6% 的连续横线（下划线）
IMG_FOOTER_REGION = 0.70          # 页高 70% 以下才可能是页脚
IMG_FOOTER_MAX_WIDTH = 0.22       # 页脚页码通常很窄（≤ 图宽×22%）
IMG_FOOTER_RUN_RATIO = 0.50       # 页脚区满宽横线（页脚分隔线）也算页脚
IMG_INDENT_MIN_RATIO = 0.6        # 判定「题目首行」的最小左缩进 = 中位行高×比例
IMG_INDENT_MAX_RATIO = 6.0        # 缩进过大（右侧插图/图注）不算题目首行
IMG_BODY_LEFT_PCT = 10            # 正文左边界取各文字行左边界的 10 分位

# ---- 数字版 PDF 里的插图（文字层只报文字位置，插图同样占版面）
IMG_BLOCK_MIN_W_PT = 12.0         # 小于该宽度的图按图标/噪点忽略
IMG_BLOCK_MIN_H_PT = 8.0
IMG_BLOCK_MIN_AREA_RATIO = 0.002  # 面积 < 页面 0.2% 的图忽略（logo/角标）
IMG_BLOCK_FULL_PAGE_RATIO = 0.92  # 覆盖 ≥92% 页面的图按「整页扫描底图」忽略
IMG_HEADER_SKIP_RATIO = 0.06      # 页眉/页脚区域的小图忽略

# ---- 渲染
SIZE_DEFAULT_PT = 12.4            # 答题默认字号（与 page_model/hwrender 默认一致）
SIZE_STEP_PT = 0.5
SIZE_MIN_PT = 8.0                 # 答题字号下限
LINE_RATIO = 1.85                 # 行距 = 字号 × 1.85
FIT_TOLERANCE_PT = 2.0            # 估算所需高度允许的误差（pt）
LINE_EST_PT = SIZE_DEFAULT_PT * LINE_RATIO   # 估算 answer_lines 用的行高 ≈ 22.94pt
DEFAULT_INK = (16, 16, 18)        # 墨色（与 hwrender.INK 一致）

# ---- 答案位置：原页答题区 / 新插入的空白页
ANSWER_MODE_INLINE = "inline"     # 写回原页面的 answer_rect（默认）
ANSWER_MODE_BLANK = "blank"       # 每页作业后插空白页写答案（题目页没有留白时用）
BLANK_ANSWER_MARGIN_PT = 56.0     # 空白答题页左右留白
BLANK_ANSWER_TOP_PT = 56.0
BLANK_ANSWER_BOTTOM_PT = 48.0
BLANK_ANSWER_GAP_PT = 18.0        # 两道题答案之间留白

# 语义硬换行标记，必须与 hwrender.HARD_BREAK 一致。
# 作用：告诉渲染器「这里写完一句/算完一步，必须另起一行」。不能直接用 '\n'，
# 因为 hwrender._paragraphs() 把单个 '\n' 当编辑器软换行合并掉。
HARD_BREAK = "\u2028"


# ======================================================================
# 小工具
# ======================================================================
def _dedupe(seq):
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _percentile(values, pct):
    if not values:
        return 0.0
    vals = sorted(values)
    k = int(round((len(vals) - 1) * float(pct) / 100.0))
    return float(vals[max(0, min(len(vals) - 1, k))])


def _median(values):
    vals = sorted(values)
    n = len(vals)
    if not n:
        return 0.0
    mid = n // 2
    return float(vals[mid]) if n % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _rect_key(rect):
    """把 rect 规整成 [l, t, r, b]（l<r、t<b）并 round 到 0.1pt。"""
    l, t, r, b = [float(v) for v in rect]
    l, r = min(l, r), max(l, r)
    t, b = min(t, b), max(t, b)
    return [round(l, 1), round(t, 1), round(r, 1), round(b, 1)]


def _union_bbox(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _abspath_existing(path, what="作业页"):
    if not isinstance(path, str) or not path.strip():
        raise ValueError("缺少%s路径（source）" % what)
    p = os.path.abspath(os.path.expanduser(os.path.expandvars(path.strip())))
    if not os.path.exists(p):
        raise FileNotFoundError("%s不存在：%s" % (what, p))
    if not os.path.isfile(p):
        raise ValueError("%s不是普通文件：%s" % (what, p))
    return p


def guess_kind(path):
    """按扩展名/文件头判断是 PDF 还是图片。"""
    ext = os.path.splitext(str(path))[1].lower()
    if ext == ".pdf":
        return "pdf"
    if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"):
        return "image"
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        if head[:5] == b"%PDF-":
            return "pdf"
    except OSError:
        pass
    raise ValueError("无法判断文件类型（只支持 PDF / PNG / JPG / WEBP / BMP / TIFF）：%s"
                     % path)


def _select_pages(page, count, kind="pdf"):
    """把 page 参数（None/"all"/int）展开成要分析的页索引列表。"""
    if kind == "image":
        if page in (None, "", "all", "ALL", "*", 0, "0", "None"):
            return [0]
        raise ValueError("图片只有 1 页，page 只能为 0 / all：%r" % (page,))
    if page is None or (isinstance(page, str) and page.strip().lower() in
                        ("", "all", "*", "-1")):
        return list(range(count))
    try:
        idx = int(page)
    except (TypeError, ValueError):
        raise ValueError("page 只能是页号（从 0 开始）或 all：%r" % (page,))
    if not (0 <= idx < count):
        raise ValueError("页码越界：%d（共 %d 页，从 0 开始）" % (idx, count))
    return [idx]


# ======================================================================
# 题号解析
# ======================================================================
_CN_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
              "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_to_int(s):
    """中文数字 → int，支持 一 ~ 九十九（题号足够用）。失败返回 None。"""
    s = (s or "").strip()
    if not s:
        return None
    if s == "十":
        return 10
    if "十" in s:
        left, _, right = s.partition("十")
        if (left and left not in _CN_DIGITS) or (right and right not in _CN_DIGITS):
            return None
        return (_CN_DIGITS.get(left, 1) if left else 1) * 10 + \
            (_CN_DIGITS.get(right, 0) if right else 0)
    if len(s) == 1:
        return _CN_DIGITS.get(s)
    n = 0
    for ch in s:
        d = _CN_DIGITS.get(ch)
        if d is None:
            return None
        n = n * 10 + d
    return n


def parse_question_number(text):
    """按题号正则解析行首题号，返回题号字符串（``"1"`` / ``"4.2.1"``）或 None。

    支持：``1.`` ``2．`` ``3、`` ``4)`` ``（5）`` ``一、`` ``十二.``
    ``4.2.1`` ``12.4.3``；中文数字换算成阿拉伯数字，方便答案文件统一用
    ``{"1": ...}`` 或 ``{"4.2.1": ...}`` 作键。
    行首小数（如「1.5倍」）不算题号。
    """
    text = text or ""
    m = _MULTI_NUM_RE.match(text)
    if m:
        return m.group(1).replace("．", ".")
    m = _SINGLE_NUM_RE.match(text)
    if m:
        return str(int(m.group(1)))
    m = _PAREN_NUM_RE.match(text)
    if m:
        return str(int(m.group(1)))
    m = _CN_NUM_RE.match(text)
    if m:
        v = _cn_to_int(m.group(1))
        return str(v) if v else None
    return None


# ======================================================================
# 数字版 PDF：文字行 / 横线
# ======================================================================
def _pdf_text_lines(page):
    """取一页的全部文字行：[{text, bbox, size}]，按 (top, left) 排序。"""
    lines = []
    try:
        data = page.get_text("dict")
    except Exception:
        return lines
    for block in data.get("blocks") or []:
        if block.get("type") != 0:      # 0=文字块，1=图片块
            continue
        for ln in block.get("lines") or []:
            spans = ln.get("spans") or []
            text = "".join(str(s.get("text") or "") for s in spans)
            if not text.strip():
                continue
            bbox = ln.get("bbox")
            if not bbox or len(bbox) != 4:
                continue
            size = max([float(s.get("size") or 0) for s in spans] or [0.0])
            lines.append({"text": text, "bbox": [float(v) for v in bbox],
                          "size": size or 12.0})
    lines.sort(key=lambda it: (round(it["bbox"][1], 1), round(it["bbox"][0], 1)))
    return lines


def _pdf_image_rects(page, page_w, page_h):
    """数字版 PDF 页里的插图矩形（pt，[l, t, r, b]）。

    文字层只告诉你「哪里有字」；几何图、电路图、表格截图同样是题目内容，
    却没有任何文字行。以前划区只看文字，答案就会直接盖在插图上。这里把
    足够大的图片块收集起来，交给题目分块「并进题目区 / 让答题区止步」。

    只过滤小图标、页眉 logo、页脚小图和整页扫描底图；规则偏保守——
    宁可在个别页把插图当成题目内容，也不要漏掉真正的图。
    """
    rects = []
    try:
        blocks = page.get_text("dict").get("blocks") or []
    except Exception:
        return rects
    page_area = max(1.0, float(page_w) * float(page_h))
    for block in blocks:
        if block.get("type") != 1:          # 1 = 图片块
            continue
        bbox = block.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        try:
            r = [float(v) for v in bbox]
        except (TypeError, ValueError):
            continue
        w, h = r[2] - r[0], r[3] - r[1]
        if w < IMG_BLOCK_MIN_W_PT or h < IMG_BLOCK_MIN_H_PT:
            continue
        if w * h < IMG_BLOCK_MIN_AREA_RATIO * page_area:
            continue
        if (w >= IMG_BLOCK_FULL_PAGE_RATIO * page_w
                and h >= IMG_BLOCK_FULL_PAGE_RATIO * page_h):
            continue                        # 整页扫描底图，交给图片模式
        if r[3] <= IMG_HEADER_SKIP_RATIO * page_h:
            continue                        # 页眉校徽/logo
        if (r[1] >= (1.0 - IMG_HEADER_SKIP_RATIO) * page_h
                and h < 0.12 * page_h):
            continue                        # 页脚小图
        rects.append([round(r[0], 1), round(r[1], 1),
                      round(r[2], 1), round(r[3], 1)])
    rects.sort(key=lambda rc: (rc[1], rc[0]))
    return rects


def _collect_gray_lines(page, page_w):
    """收集一页里的「水平横线」（含浅灰虚线），返回 [{y, x0, x1, gray}]。

    数字版作业纸的答题横线通常是 draw_line 画出来的，PyMuPDF 里表现为
    get_drawings() 的 'l'（线段）或很扁的 're'（矩形）。只保留近水平、
    长度 ≥ MIN_GRAY_LINE_LEN 的线；颜色不限（浅灰 / 黑都能当答题横线），
    ``gray`` 记录平均色深，方便调试与后续过滤。
    """
    out = []
    try:
        drawings = page.get_drawings()
    except Exception:
        return out
    for dr in drawings:
        color = dr.get("color") or dr.get("fill")
        for item in dr.get("items") or []:
            if not item:
                continue
            kind = item[0]
            if kind == "l" and len(item) >= 3:
                p1, p2 = item[1], item[2]
                y1, y2 = float(p1.y), float(p2.y)
                if abs(y1 - y2) > 1.0:
                    continue
                x0, x1 = sorted((float(p1.x), float(p2.x)))
            elif kind == "re" and len(item) >= 2:
                r = item[1]
                if float(r.height) > 1.5 or float(r.width) < MIN_GRAY_LINE_LEN:
                    continue
                x0, x1 = sorted((float(r.x0), float(r.x1)))
                y1 = y2 = (float(r.y0) + float(r.y1)) / 2.0
            else:
                continue
            if x1 - x0 < MIN_GRAY_LINE_LEN:
                continue
            gray = -1.0
            if color is not None:
                try:
                    vals = ([float(c) for c in color]
                            if isinstance(color, (list, tuple)) else [float(color)])
                    if vals:
                        gray = sum(vals) / len(vals)
                except (TypeError, ValueError):
                    gray = -1.0
            out.append({"y": (y1 + y2) / 2.0, "x0": x0, "x1": x1, "gray": gray})
    # 同一条线可能被拆成多段/重复绘制：按 y 合并
    out.sort(key=lambda it: it["y"])
    merged = []
    for ln in out:
        if merged and abs(ln["y"] - merged[-1]["y"]) <= GRAY_LINE_YDUP_PT:
            merged[-1]["y"] = (merged[-1]["y"] + ln["y"]) / 2.0
            merged[-1]["x0"] = min(merged[-1]["x0"], ln["x0"])
            merged[-1]["x1"] = max(merged[-1]["x1"], ln["x1"])
            if merged[-1]["gray"] < 0:
                merged[-1]["gray"] = ln["gray"]
        else:
            merged.append(dict(ln))
    return [ln for ln in merged
            if ln["x1"] > 0 and ln["x0"] < page_w
            and ln["x1"] - ln["x0"] >= MIN_GRAY_LINE_LEN]


# ======================================================================
# 答题区分配（数字版与扫描件共用）
# ======================================================================
def estimate_answer_lines(height_pt):
    """按答题区高度估算可写行数（12.4pt 正文、行距 1.85 倍）。"""
    return max(1, int((max(0.0, float(height_pt)) - 4.0) / LINE_EST_PT))


def _trim_region_to_obstacles(top, bottom, obstacles, pad=2.0):
    """把答题区 [top, bottom] 裁到不与插图重叠；图在中间时保留较大的一侧。"""
    for _ in range(len(obstacles or []) + 2):
        hit = None
        for rect in obstacles or []:
            if rect[3] <= top + 0.5 or rect[1] >= bottom - 0.5:
                continue
            hit = rect
            break
        if hit is None:
            return top, bottom
        ot, ob = hit[1], hit[3]
        if ot <= top + 0.5:
            top = ob + pad
        elif ob >= bottom - 0.5:
            bottom = ot - pad
        elif (ob - top) >= (bottom - ot):
            bottom = ot - pad
        else:
            top = ob + pad
        if bottom <= top + 1.0:
            return top, top
    return top, bottom


def _finalize_answer_regions(stubs, page_w, page_h, page_no, warnings,
                             lines_in=None, image_mode=False,
                             content_left=0.0, content_right=None,
                             obstacles=None):
    """给每个题目块定 answer_rect / answer_lines（就地修改 stubs）。

    规则：
    · 默认「每题的自然答题区」= 本题下方到下一题（或页底/页脚）之间的空白；
    · 若某题自然答题区 < MIN_ANSWER_H_PT（题目上下排布很紧，常见于扫描的
      教材/试卷页：题干都在页顶、下面是整片空白），则改为「整页空白区按题
      顺序从上往下分配」，这样答案不会压到下一题题干；
    · obstacles（数字版 PDF 的插图矩形）参与避让：分配后再裁一次，答案不压图；
    · answer_lines：区域内有识别到横线 → 横线条数；否则按高度估算。
    """
    if not stubs:
        return stubs
    if content_right is None:
        content_right = page_w - 2.0

    heights, tops = [], []
    for s in stubs:
        pad = s.get("pad", ANSWER_PAD_MIN_PT)
        top = s["rect"][3] + pad
        bottom = s.get("limit", page_h)
        if bottom <= top + 2.0:
            heights.append(0.0)
            tops.append(top)
        else:
            heights.append(bottom - top)
            tops.append(top)

    stacked = any(h < MIN_ANSWER_H_PT for h in heights)
    regions = []
    if stacked:
        last = stubs[-1]
        blank_top = last["rect"][3] + last.get("pad", ANSWER_PAD_MIN_PT)
        blank_bottom = last.get("limit", page_h)
        gap = STACK_GAP_PT if len(stubs) > 1 else 0.0
        total = blank_bottom - blank_top - gap * (len(stubs) - 1)
        each = total / float(len(stubs))
        if each < MIN_ANSWER_H_PT:
            # 空白区实在太小：保住最小高度（不丢题），并提示
            each = min(MIN_ANSWER_H_PT, max(4.0, total / float(len(stubs))))
            warnings.append("第 %d 页答题空白区过小（%.0fpt），各题答题区按最小高度拆分"
                            % (page_no + 1, max(0.0, total)))
        for i in range(len(stubs)):
            t = blank_top + i * (each + gap)
            b = t + each
            if obstacles:
                t, b = _trim_region_to_obstacles(t, b, obstacles)
            regions.append((t, b))
        if len(stubs) > 1:
            warnings.append(
                "第 %d 页题目排布较紧，答题区改为按整页空白区顺序分配"
                "（自上而下第 1..%d 题）" % (page_no + 1, len(stubs)))
    else:
        regions = [(tops[i], tops[i] + heights[i]) for i in range(len(stubs))]

    for i, s in enumerate(stubs):
        top, bottom = regions[i]
        cand = []
        if not image_mode and lines_in is not None:
            cand = lines_in((0.0, top, page_w, bottom)) or []
        if cand:
            left = min(c["x0"] for c in cand)
            right = max(c["x1"] for c in cand)
        else:
            left = content_left
            # 扫描页正文往往只有左边一部分：按「左右页边距对称」补足书写宽度
            right = max(content_right, page_w - content_left)
        left = _clamp(left, 1.0, page_w - 2.0)
        right = _clamp(max(right, left + MIN_ANSWER_W_PT), 2.0, page_w - 1.0)
        rect = _rect_key([left, top, right, bottom])
        ans_h = rect[3] - rect[1]
        ans_w = rect[2] - rect[0]
        if cand:
            answer_lines = len(cand)
        else:
            answer_lines = estimate_answer_lines(ans_h)
        if ans_h < MIN_ANSWER_H_PT:
            warnings.append("第 %d 页第 %s 题答题区过小（高 %.1fpt < %.0fpt），"
                            "建议人工确认" % (page_no + 1, s.get("number") or "?",
                                              ans_h, MIN_ANSWER_H_PT))
        if ans_w < MIN_ANSWER_W_PT:
            warnings.append("第 %d 页第 %s 题答题区过窄（宽 %.1fpt）"
                            % (page_no + 1, s.get("number") or "?", ans_w))
        s["answer_rect"] = rect
        s["answer_lines"] = answer_lines
        s["_lines_detected"] = bool(cand)
    return stubs


# ======================================================================
# 数字版 PDF：题目块
# ======================================================================
def _pdf_question_stubs(lines, page_w, page_h, page_no, warnings,
                        image_rects=None):
    """从一页的文字行里识别题目块（返回 stubs：rect/text/number/pad/limit）。

    image_rects 是 `_pdf_image_rects()` 找到的插图：紧随题干的插图会并进本题
    的 rect，答题区的下边界也会在下一张插图前止步（答案不压图）。
    """
    starts = [i for i, ln in enumerate(lines)
              if parse_question_number(ln["text"]) is not None]
    if not starts:
        return []
    if len(starts) > MAX_QUESTIONS_PER_PAGE:
        warnings.append("第 %d 页识别到 %d 个题号，超过单页上限 %d，只取前 %d 个"
                        % (page_no + 1, len(starts), MAX_QUESTIONS_PER_PAGE,
                           MAX_QUESTIONS_PER_PAGE))
        starts = starts[:MAX_QUESTIONS_PER_PAGE]
    start_set = set(starts)
    image_rects = list(image_rects or [])
    image_claimed = set()
    line_h = _median([max(1.0, ln["bbox"][3] - ln["bbox"][1]) for ln in lines])
    pad = _clamp(line_h * ANSWER_PAD_RATIO, ANSWER_PAD_MIN_PT, ANSWER_PAD_MAX_PT)
    bottom_margin = _clamp(page_h * ANSWER_BOTTOM_MARGIN_RATIO,
                           ANSWER_BOTTOM_MARGIN_MIN, ANSWER_BOTTOM_MARGIN_MAX)

    stubs = []
    for k, si in enumerate(starts):
        block = [lines[si]]
        j = si + 1
        while j < len(lines) and j not in start_set:
            prev = block[-1]["bbox"]
            cur = lines[j]["bbox"]
            gap = cur[1] - prev[3]          # 行间距（上行底 → 下行顶）
            same_col = (min(cur[2], prev[2]) - max(cur[0], prev[0])) > 0
            if gap > max(CONTINUATION_GAP_MIN_PT,
                         line_h * CONTINUATION_GAP_RATIO) or not same_col:
                break
            block.append(lines[j])
            j += 1
        qrect = _rect_key(_union_bbox([ln["bbox"] for ln in block]))
        qtext = "".join(ln["text"].strip() for ln in block).strip()
        number = parse_question_number(block[0]["text"])
        next_top = (lines[starts[k + 1]]["bbox"][1]
                    if k + 1 < len(starts) else page_h)
        # 题干下方/旁边的插图属于本题：并进题目区，答案从图下面开始写
        for ii, ir in enumerate(image_rects):
            if ii in image_claimed:
                continue
            if ir[1] >= next_top - 1.0:
                continue                    # 图在下一题的位置，归下一题
            if ir[3] <= qrect[1] + 1.0:
                continue                    # 整张图都在本题题干上方
            image_claimed.add(ii)
            qrect = _rect_key(_union_bbox([qrect, ir]))
        # 本题下方第一条「不属于本题块」的文字行（通常就是下一题/页脚）
        below = [ln for ln in lines if ln["bbox"][1] > qrect[3] + 0.5]
        if below:
            limit = min(ln["bbox"][1] for ln in below) - pad
        else:
            limit = page_h - bottom_margin
        # 答题区也不能越过下一题（否则插图把 rect 拉长时会压到下一题）
        if k + 1 < len(starts):
            limit = min(limit, next_top - pad)
        # 本题下方还有没被任何题认领的插图：答题区在它上面止步
        below_imgs = [ir for ii, ir in enumerate(image_rects)
                      if ii not in image_claimed and ir[1] > qrect[3] + 0.5]
        if below_imgs:
            limit = min(limit, min(ir[1] for ir in below_imgs) - pad)
        stubs.append({"rect": qrect, "text": qtext, "number": number,
                      "pad": pad, "limit": limit})
    return stubs


# ======================================================================
# 图片 / 扫描件：墨迹投影 + 连通域
# ======================================================================
def _runs(flags):
    """连续 True 的区间列表 [(start, end_inclusive), ...]。"""
    out, start = [], None
    for i, flag in enumerate(flags):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            out.append((start, i - 1))
            start = None
    if start is not None:
        out.append((start, len(flags) - 1))
    return out


def _scan_text_mask(gray):
    """灰度图 → 去噪后的墨迹二值掩码（numpy bool）。"""
    import numpy as np
    from scipy import ndimage as ndi

    mask = np.asarray(gray, dtype=np.uint8) < SCAN_TEXT_THRESHOLD
    if not mask.any():
        return mask
    lab, n = ndi.label(mask, structure=np.ones((3, 3), dtype=int))
    if n:
        sizes = np.bincount(lab.ravel())
        keep = np.zeros(sizes.shape[0], dtype=bool)
        keep[1:] = sizes[1:] >= SCAN_MIN_COMPONENT_PX
        mask = keep[lab]
    return mask


def _max_row_run(sub_mask):
    """子掩码里「某一行的最长连续墨迹长度」（用于识别表头下划线）。"""
    import numpy as np

    best = 0
    for r in range(sub_mask.shape[0]):
        row = sub_mask[r].astype(np.int8)
        padded = np.concatenate(([0], row, [0]))
        d = np.diff(padded)
        starts = np.nonzero(d == 1)[0]
        ends = np.nonzero(d == -1)[0]
        if len(starts):
            best = max(best, int((ends - starts).max()))
    return best


def _image_blocks(img, page_w, page_h, dpi, page_no):
    """扫描页 → (question_blocks, footer_limit_pt, content_left_pt,
    content_right_pt, warnings)。

    步骤：墨迹二值化 → 行投影成「文字行」→ 表头（顶部含长下划线）/页脚
    （底部窄页码、满宽分隔线）排除 → 用首行缩进判断题目起始行、续行并入
    同一题（无缩进时退回「行间距」分块）。
    """
    import numpy as np

    warnings = []
    rgb = img.convert("RGB")
    gray = np.asarray(rgb.convert("L"), dtype=np.uint8)
    h_px, w_px = gray.shape
    if not h_px or not w_px:
        return [], page_h, 0.0, page_w, ["第 %d 页图片为空" % (page_no + 1)]
    mask = _scan_text_mask(gray)
    if not mask.any():
        return [], page_h, 0.0, page_w, ["第 %d 页没有检测到墨迹（空白页？）"
                                         % (page_no + 1)]

    rows = mask.sum(axis=1)
    thr = max(SCAN_ROW_THRESHOLD_MIN,
              int(round(w_px * SCAN_ROW_THRESHOLD_RATIO)))
    bands = _runs(rows >= thr)
    merged = []
    for s, e in bands:
        if merged and s - merged[-1][1] <= SCAN_BAND_MERGE_GAP_PX:
            merged[-1] = (merged[-1][0], e)
        else:
            merged.append((s, e))

    band_info = []
    for s, e in merged:
        sub = mask[s:e + 1]
        cols = sub.sum(axis=0)
        xs = np.nonzero(cols > 0)[0]
        if not len(xs):
            continue
        x0, x1 = int(xs[0]), int(xs[-1])
        # 每个字符/下划线都是一个连通域，去掉「太窄太小」的零散噪点行
        if x1 - x0 < max(8, int(round(w_px * 0.01))):
            continue
        band_info.append({"top": s, "bottom": e, "left": x0, "right": x1,
                          "maxrun": _max_row_run(sub)})
    if not band_info:
        return [], page_h, 0.0, page_w, ["第 %d 页墨迹太少，无法分块"
                                         % (page_no + 1)]

    # ---- 表头：页顶 15% 内、以「长下划线」为特征的第一组行 ----
    header_end = -1
    for i, b in enumerate(band_info):
        if b["top"] > IMG_HEADER_REGION * h_px:
            break
        if b["maxrun"] >= IMG_HEADER_RUN_RATIO * w_px:
            header_end = i
            break
    header = band_info[:header_end + 1]
    rest = band_info[len(header):]

    # ---- 页脚：页高 70% 以下 + （窄页码 或 满宽横线） ----
    footer_ids = set()
    for i, b in enumerate(rest):
        if b["top"] <= IMG_FOOTER_REGION * h_px:
            continue
        width = b["right"] - b["left"]
        if width < IMG_FOOTER_MAX_WIDTH * w_px \
                or b["maxrun"] >= IMG_FOOTER_RUN_RATIO * w_px:
            footer_ids.add(id(b))
    footer = [b for b in rest if id(b) in footer_ids]
    content = [b for b in rest if id(b) not in footer_ids]
    if not content:
        return [], page_h, 0.0, page_w, \
            ["第 %d 页只识别到表头/页脚，没有题目内容" % (page_no + 1)]
    if header:
        warnings.append("第 %d 页已排除表头区域（%d 行，含下划线/首行位置）"
                        % (page_no + 1, len(header)))

    scale_x = page_w / float(w_px)
    scale_y = page_h / float(h_px)
    band_h_px = _median([b["bottom"] - b["top"] + 1 for b in content]) or 20.0
    content_left = min(b["left"] for b in content)
    content_right = max(b["right"] for b in content)

    # ---- 题目起始行：左缩进明显大于正文左边界（换行续行不缩进） ----
    body_left = _percentile([b["left"] for b in content],
                            IMG_BODY_LEFT_PCT)
    indent_min = max(6.0, band_h_px * IMG_INDENT_MIN_RATIO)
    indent_max = band_h_px * IMG_INDENT_MAX_RATIO
    start_idx = set()
    for i, b in enumerate(content):
        indent = b["left"] - body_left
        if indent_min <= indent <= indent_max:
            start_idx.add(i)

    blocks = []
    cur = None
    for i, b in enumerate(content):
        is_start = i in start_idx
        if is_start:
            if cur:
                blocks.append(cur)
            cur = [b]
        elif cur is None:
            # 首题之前零散的行（可能漏检的表头/分隔线）：忽略
            continue
        else:
            gap = b["top"] - cur[-1]["bottom"]
            if gap > max(SCAN_BLOCK_MERGE_MIN_PX,
                         band_h_px * QUESTION_GROUP_GAP_RATIO):
                blocks.append(cur)
                cur = [b]
            else:
                cur.append(b)
    if cur:
        blocks.append(cur)

    if not start_idx:
        # 没有可用的缩进特征：退回「行间距」分块（样张 A 那种每题带横线的版式）
        blocks, cur = [], [content[0]]
        for b in content[1:]:
            gap = b["top"] - cur[-1]["bottom"]
            if gap <= max(SCAN_BLOCK_MERGE_MIN_PX,
                          band_h_px * SCAN_BAND_MERGE_RATIO):
                cur.append(b)
            else:
                blocks.append(cur)
                cur = [b]
        blocks.append(cur)

    if len(blocks) > SCAN_MAX_QUESTIONS:
        warnings.append("第 %d 页候选题块 %d 个，超过上限 %d，只取前 %d 个"
                        % (page_no + 1, len(blocks), SCAN_MAX_QUESTIONS,
                           SCAN_MAX_QUESTIONS))
        blocks = blocks[:SCAN_MAX_QUESTIONS]

    footer_top = min((b["top"] for b in footer), default=None)
    bottom_margin = _clamp(page_h * ANSWER_BOTTOM_MARGIN_RATIO,
                           ANSWER_BOTTOM_MARGIN_MIN, ANSWER_BOTTOM_MARGIN_MAX)
    footer_limit = (footer_top * scale_y) if footer_top is not None \
        else (page_h - bottom_margin)
    blocks_out = []
    for blk in blocks:
        rect = _rect_key([min(b["left"] for b in blk) * scale_x,
                          min(b["top"] for b in blk) * scale_y,
                          max(b["right"] for b in blk) * scale_x,
                          (max(b["bottom"] for b in blk) + 1) * scale_y])
        blocks_out.append({"rect": rect, "px": blk})
    return blocks_out, footer_limit, content_left * scale_x, \
        content_right * scale_x, warnings


def _image_questions(img, page_w, page_h, dpi, page_no):
    """扫描/照片页 → 题目列表（text=None + 裁剪 PNG + 答题区）。"""
    import numpy as np

    blocks, footer_limit, content_left, content_right, warnings = \
        _image_blocks(img, page_w, page_h, dpi, page_no)
    if not blocks:
        return [], warnings
    rgb = img.convert("RGB")
    w_px, h_px = rgb.size
    scale_x = page_w / float(w_px)
    scale_y = page_h / float(h_px)
    pad_pt = ANSWER_PAD_MIN_PT
    # 宽/高比例按内容区算，避免扫描件四周留白不同
    band_heights_px = [b["bottom"] - b["top"] + 1
                       for blk in blocks for b in blk["px"]]
    band_h_px = _median(band_heights_px) or 20.0
    pad_pt = _clamp(band_h_px * scale_y * ANSWER_PAD_RATIO,
                    ANSWER_PAD_MIN_PT, ANSWER_PAD_MAX_PT)
    crop_pad_px = max(SCAN_CROP_PAD_PX, int(round(band_h_px * 0.5)))

    stubs = []
    for i, blk in enumerate(blocks):
        limit = footer_limit - pad_pt          # 最后一块：到页脚上方留白
        pad_next = pad_pt
        if i + 1 < len(blocks):
            nxt = blocks[i + 1]
            limit = nxt["rect"][1] - pad_next
        # 题目区域裁剪：x 覆盖整行正文宽度，y 覆盖题目块（含跨行续行/插图）
        cy0 = int(_clamp(min(b["top"] for b in blk["px"]) - crop_pad_px,
                         0, h_px - 1))
        cy1 = int(_clamp(max(b["bottom"] for b in blk["px"]) + 1 + crop_pad_px,
                         cy0 + 4, h_px))
        cx0 = int(_clamp(content_left * w_px / page_w - crop_pad_px, 0, w_px - 1))
        cx1 = int(_clamp(content_right * w_px / page_w + crop_pad_px,
                         cx0 + 8, w_px))
        crop = rgb.crop((cx0, cy0, cx1, cy1))
        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        stubs.append({
            "rect": blk["rect"],
            "text": None,
            "number": "",
            "pad": pad_pt,
            "limit": limit,
            "crop_png_base64": base64.b64encode(buf.getvalue()).decode("ascii"),
        })

    _finalize_answer_regions(stubs, page_w, page_h, page_no, warnings,
                             lines_in=None, image_mode=True,
                             content_left=content_left,
                             content_right=content_right)
    warnings.append(
        "第 %d 页为扫描件（无文字层）：题号不可识别，按顺序编号；"
        "answer_lines 为按空白高度估算的可写行数" % (page_no + 1))
    questions = []
    for s in stubs:
        confidence = 0.42
        if s["rect"][2] - s["rect"][0] >= 0.3 * page_w:
            confidence += 0.10
        if s["answer_rect"][3] - s["answer_rect"][1] >= 2 * MIN_ANSWER_H_PT:
            confidence += 0.05
        questions.append({
            "id": "",
            "number": s["number"] or "",
            "text": None,
            "rect": s["rect"],
            "answer_rect": s["answer_rect"],
            "answer_lines": s["answer_lines"],
            "confidence": round(min(0.75, confidence), 2),
            "crop_png_base64": s["crop_png_base64"],
        })
    return questions, warnings


# ======================================================================
# analyze：入口
# ======================================================================
def _assign_ids(pages):
    """给所有题分配唯一 id；跨页重复题号时追加页后缀（Q1_p2）。"""
    flat = []
    for pg in pages:
        for q in pg["questions"]:
            q["_page"] = pg["page"]
            flat.append(q)
    base_count = {}
    for q in flat:
        base_count[q["number"]] = base_count.get(q["number"], 0) + 1
    seen = {}
    for q in flat:
        base = "Q" + str(q["number"])
        if base_count.get(q["number"], 1) <= 1:
            q["id"] = base
            continue
        seen[base] = seen.get(base, 0) + 1
        q["id"] = "%s_p%d" % (base, q["_page"] + 1)
        if seen[base] > 1:
            q["id"] += "_%d" % seen[base]
    for q in flat:
        q.pop("_page", None)


def _fill_number_hints(pages, numbers):
    """把用户给的题号序列（真实 PDF 没有 OCR 时用）依次套到各题上。"""
    if numbers is None:
        return []
    if isinstance(numbers, str):
        items = [x for x in re.split(r"[,，;；\s]+", numbers.strip()) if x]
    elif isinstance(numbers, (list, tuple)):
        items = [str(x).strip() for x in numbers if str(x).strip()]
    else:
        raise ValueError("numbers 必须是数组或以逗号分隔的字符串")
    qs = [q for pg in pages for q in pg["questions"]]
    warnings = []
    if not items:
        return warnings
    if len(items) < len(qs):
        warnings.append("numbers 只给了 %d 个题号，少于识别到的 %d 题，"
                        "剩余的按顺序编号" % (len(items), len(qs)))
    applied = items[:len(qs)]
    for q, num in zip(qs, applied):
        q["number"] = num
    if len(qs) > len(applied):
        used = set(applied)
        n = 1
        for q in qs[len(applied):]:
            while str(n) in used:
                n += 1
            q["number"] = str(n)
            used.add(str(n))
            n += 1
    return warnings


def _analyze_pdf(path, page=None, dpi=SCAN_DPI_DEFAULT):
    import pymupdf as fitz

    try:
        doc = fitz.open(path)
    except Exception as e:
        raise ValueError("PDF 无法打开：%s（%s）" % (path, e))
    if doc.page_count < 1:
        doc.close()
        raise ValueError("PDF 没有页面：%s" % path)
    try:
        indices = _select_pages(page, doc.page_count, "pdf")
        pages = []
        for idx in indices:
            pg = doc[idx]
            page_w = float(pg.rect.width)
            page_h = float(pg.rect.height)
            page_warn = []
            lines = _pdf_text_lines(pg)
            if not lines:
                # 没有文字层：当扫描页处理（PDF 里嵌了整页图片的情况很常见）
                pix = pg.get_pixmap(dpi=dpi, alpha=False)
                from PIL import Image
                img = Image.frombytes("RGB", (pix.width, pix.height),
                                      pix.samples)
                questions, scan_warn = _image_questions(img, page_w, page_h,
                                                        dpi, idx)
                page_warn.append(
                    "第 %d 页没有文字层（扫描/图片页），已按像素投影分块；"
                    "question_text=null，请用 crop_png_base64 让 AI 读图或提供 OCR"
                    % (idx + 1))
                page_warn.extend(scan_warn)
            else:
                image_rects = _pdf_image_rects(pg, page_w, page_h)
                stubs = _pdf_question_stubs(lines, page_w, page_h, idx,
                                            page_warn,
                                            image_rects=image_rects)
                if stubs:
                    content_left = min(ln["bbox"][0] for ln in lines)
                    content_right = max(ln["bbox"][2] for ln in lines)
                    gray_lines = _collect_gray_lines(pg, page_w)

                    def _lines_in(rect, _gl=gray_lines):
                        return [c for c in _gl
                                if rect[1] + 0.5 <= c["y"] <= rect[3] - 0.5
                                and c["x1"] > rect[0] + 2
                                and c["x0"] < rect[2] - 2]

                    _finalize_answer_regions(
                        stubs, page_w, page_h, idx, page_warn,
                        lines_in=_lines_in, image_mode=False,
                        content_left=content_left, content_right=content_right,
                        obstacles=image_rects)
                    if image_rects:
                        page_warn.append(
                            "第 %d 页识别到 %d 处插图：已并入题目区，"
                            "答题区自动避让，不会把答案写在图上"
                            % (idx + 1, len(image_rects)))
                    for s in stubs:
                        if not s.get("_lines_detected", False):
                            page_warn.append(
                                "第 %d 页第 %s 题未识别到答题横线，"
                                "答题区按空白区域估算"
                                % (idx + 1, s.get("number") or "?"))
                    questions = [{
                        "id": "",
                        "number": s["number"],
                        "text": s["text"],
                        "rect": s["rect"],
                        "answer_rect": s["answer_rect"],
                        "answer_lines": s["answer_lines"],
                        "confidence": _pdf_confidence(s, page_w),
                    } for s in stubs]
                else:
                    questions = []
                    page_warn.append(
                        "第 %d 页有文字层但未识别到题号（正则不匹配或版式特殊），"
                        "可换 --page 或人工确认" % (idx + 1))
            pages.append({
                "page": idx,
                "width_pt": round(page_w, 2),
                "height_pt": round(page_h, 2),
                "questions": questions,
                "warnings": _dedupe(page_warn),
            })
    finally:
        doc.close()
    return pages


def _pdf_confidence(stub, page_w):
    rect = stub["rect"]
    ans = stub["answer_rect"]
    ans_h = ans[3] - ans[1]
    conf = 0.55
    if stub.get("text"):
        conf += 0.12
    if rect[2] - rect[0] >= 80:
        conf += 0.08
    if ans_h >= 2 * MIN_ANSWER_H_PT:
        conf += 0.10
    elif ans_h >= MIN_ANSWER_H_PT:
        conf += 0.03
    if stub.get("_lines_detected"):
        conf += 0.10
    return round(min(0.98, conf), 2)


def _analyze_image(path, page=None, dpi=None):
    from PIL import Image

    try:
        img = Image.open(path)
        img.load()
        img = img.convert("RGB")
    except Exception as e:
        raise ValueError("图片无法读取：%s（%s）" % (path, e))
    _select_pages(page, 1, "image")
    dpi_used = _image_dpi(img, dpi)
    page_w = round(img.width * 72.0 / dpi_used, 2)
    page_h = round(img.height * 72.0 / dpi_used, 2)
    questions, page_warn = _image_questions(img, page_w, page_h, dpi_used, 0)
    page_warn = _dedupe(
        ["图片模式：无文字层，question_text=null，"
         "请用 crop_png_base64 让 AI 多模态读题或提供 OCR"] + page_warn)
    return [{"page": 0, "width_pt": page_w, "height_pt": page_h,
             "questions": questions, "warnings": page_warn}]


def _image_dpi(img, override=None):
    """推断图片 dpi：显式参数 > 图片元数据 > 默认 200。"""
    if override:
        try:
            v = float(override)
            if 50 <= v <= 1200:
                return v
        except (TypeError, ValueError):
            pass
    info = getattr(img, "info", None) or {}
    dpi = info.get("dpi")
    if isinstance(dpi, (tuple, list)) and len(dpi) >= 1:
        try:
            v = float(dpi[0])
            if 50 <= v <= 1200:
                return v
        except (TypeError, ValueError):
            pass
    return float(SCAN_DPI_DEFAULT)


def analyze(source, page=None, dpi=None, numbers=None):
    """导入作业页，自动识别题目/答题区域，返回 spec（dict）。

    参数
    ----
    source  : PDF 或图片路径（数字版 PDF / 扫描件 / 照片均可）
    page    : PDF 页号（0 起）或 "all"；图片只支持 0/all
    dpi     : 扫描件/图片的 dpi 估计值（图片元数据里没有时用，默认 200）
    numbers : 可选，按顺序覆盖题号（如 "4.2.1,4.2.2,..."）；扫描件无法 OCR
              出题号时，用它把真实题号写进 spec，答案文件就能直接按题号匹配。
    """
    path = _abspath_existing(source, "作业页")
    kind = guess_kind(path)
    if kind == "pdf":
        pages = _analyze_pdf(path, page=page,
                             dpi=dpi or SCAN_PDF_DPI)
        warnings = []
    else:
        pages = _analyze_image(path, page=page, dpi=dpi)
        warnings = []
    # 扫描页题号为顺序占位；如果有没填的题号，按顺序补一个不冲突的数字
    used = {str(q.get("number")) for pg in pages
            for q in pg["questions"] if str(q.get("number") or "").strip()}
    auto = 1
    for pg in pages:
        for q in pg["questions"]:
            if str(q.get("number") or "").strip():
                continue
            while str(auto) in used:
                auto += 1
            q["number"] = str(auto)
            used.add(str(auto))
            auto += 1
    warnings.extend(_fill_number_hints(pages, numbers))
    for pg in pages:
        if not pg["questions"]:
            warnings.append("第 %d 页没有识别到题目区域" % (pg["page"] + 1))
    _assign_ids(pages)
    total = sum(len(p["questions"]) for p in pages)
    if any(q.get("text") is None for pg in pages for q in pg["questions"]):
        warnings.append(
            "存在扫描页/图片页（无文字层）：question_text=null，"
            "请用 crop_png_base64 让 AI 读图或提供 OCR；可用 numbers 指定真实题号")
    if kind == "pdf" and total == 0:
        warnings.append("没有识别到任何题目：确认页面是作业/试卷；"
                        "扫描页可提供 --numbers 或先用图片模式分析")
        warnings.append("tip：可将该 PDF 页渲染成图片后用图片模式分析")
    return {
        "source": path,
        "kind": kind,
        "version": SPEC_VERSION,
        "pages": pages,
        "warnings": _dedupe(warnings),
    }


def questions_brief(spec):
    """spec → 精简题目列表（HTTP /worksheet/analyze 响应、CLI 摘要用）。"""
    out = []
    for pg in spec.get("pages") or []:
        for q in pg.get("questions") or []:
            out.append({
                "id": q.get("id"),
                "number": q.get("number"),
                "page": pg.get("page", 0),
                "text": q.get("text"),
                "rect": q.get("rect"),
                "answer_rect": q.get("answer_rect"),
                "answer_lines": q.get("answer_lines", 0),
                "confidence": q.get("confidence", 0.0),
            })
    return out


def collect_crops(spec, max_crops=60):
    """spec 里所有扫描页题目裁剪 → [{id, page, png_base64}]。"""
    crops = []
    for pg in spec.get("pages") or []:
        for q in pg.get("questions") or []:
            b64 = q.get("crop_png_base64")
            if b64:
                crops.append({"id": q.get("id"), "page": pg.get("page", 0),
                              "format": "png", "png_base64": b64})
                if len(crops) >= max_crops:
                    return crops
    return crops


def format_spec(spec, show_text=True):
    """把 spec 格式化成中文多行摘要（CLI stdout / 日志用）。"""
    lines = ["来源: %s" % spec.get("source", ""),
             "类型: %s（%d 页）" % (
                 "PDF（数字版/含扫描页）" if spec.get("kind") == "pdf"
                 else "图片/扫描件",
                 len(spec.get("pages") or []))]
    total = 0
    for pg in spec.get("pages") or []:
        qs = pg.get("questions") or []
        total += len(qs)
        lines.append("")
        lines.append("第 %d 页  %.0f×%.0fpt  识别 %d 题"
                     % (pg.get("page", 0) + 1, pg.get("width_pt", 0),
                        pg.get("height_pt", 0), len(qs)))
        for q in qs:
            head = "  %-6s 题号 %-6s  置信度 %.2f" % (
                q.get("id", ""), q.get("number", ""),
                q.get("confidence", 0.0))
            if show_text:
                text = (q.get("text") or "（无文字层，需 AI 读图/OCR）")
                text = text if len(text) <= 60 else text[:60] + "…"
                head += "  题目: " + text
            lines.append(head)
            lines.append("       题目区 %s  答题区 %s  可写约 %d 行"
                         % (q.get("rect"), q.get("answer_rect"),
                            q.get("answer_lines", 0)))
        for w in pg.get("warnings") or []:
            lines.append("  [warning] " + w)
    lines.append("")
    lines.append("合计: %d 题" % total)
    for w in spec.get("warnings") or []:
        lines.append("[warning] " + w)
    return "\n".join(lines)


# ======================================================================
# 答案 → 文本映射
# ======================================================================
def _iter_questions(spec):
    for pg in spec.get("pages") or []:
        for q in pg.get("questions") or []:
            yield pg, q


def _answers_lookup(questions):
    by_id, by_num = {}, {}
    for q in questions:
        qid = str(q.get("id") or "")
        if qid:
            by_id[qid] = q
            by_id[qid.lower()] = q
        num = str(q.get("number") or "")
        if num:
            by_num.setdefault(num, []).append(q)
    return by_id, by_num


def _resolve_answer_key(key, by_id, by_num):
    """把答案键解析成题目 dict；解析不到返回 None。"""
    k = str(key).strip()
    if not k:
        return None
    if k in by_id:
        return by_id[k]
    if k.lower() in by_id:
        return by_id[k.lower()]
    if k in by_num:
        return by_num[k][0]
    for cand in (k.lstrip("#"), k.upper(), k.lstrip("Qq")):
        if cand in by_id:
            return by_id[cand]
        if cand in by_num:
            return by_num[cand][0]
    return None


def normalize_answers(answers, questions):
    """把 answers 统一成 {题目 id: 文本}。

    支持两种写法：
    · {"1": "解：...", "Q2": "...", "4.2.1": "..."}（题号或 id 作键；也接受
      AI 输出 {"answers": {...}} 的外层包装）；
    · [{"id": "Q1", "text": "..."}, {"number": "2", "text": "..."}]。

    扫描件题号是顺序占位（图片模式无法 OCR）：若答案键一个都对不上、且数量
    与题目数一致，则按题目顺序一一对应（并给 warning），这样 AI/用户仍可按
    真实题号顺序给答案。
    """
    by_id, by_num = _answers_lookup(questions)
    matches, warnings, unmatched = {}, [], []
    raw_items = []
    if isinstance(answers, dict):
        if isinstance(answers.get("answers"), dict):
            answers = answers["answers"]
        raw_items = list(answers.items())
    elif isinstance(answers, (list, tuple)):
        for i, item in enumerate(answers):
            if isinstance(item, dict):
                key = (item.get("id") or item.get("number")
                       or item.get("question") or item.get("q"))
                text = item.get("text")
                if text is None:
                    text = item.get("answer")
                if text is None:
                    text = item.get("content")
                raw_items.append((key if key is not None else str(i + 1), text))
            elif isinstance(item, str):
                raw_items.append((str(i + 1), item))
            else:
                warnings.append("answers[%d] 不是对象/字符串，已忽略" % i)
    else:
        raise ValueError("answers 必须是 JSON 对象或数组")

    pending = []
    for key, value in raw_items:
        q = _resolve_answer_key(key, by_id, by_num)
        if q is None:
            pending.append((str(key), value))
            continue
        if value is None:
            warnings.append("题目 %s 的答案为空，已跳过" % q.get("id"))
            continue
        text = value if isinstance(value, str) else str(value)
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
        if not text:
            warnings.append("题目 %s 的答案为空，已跳过" % q.get("id"))
            continue
        qid = str(q.get("id"))
        if qid in matches:
            warnings.append("题目 %s 收到多份答案，沿用第一份" % qid)
            continue
        matches[qid] = text

    # 扫描件兜底：答案键没匹配上 → 按题目顺序对应。
    # 仅在「键全是数字型题号」且「未匹配键数 == 未作答题目数」时启用，
    # 避免把 {"99": ...} 这种错键硬塞给第一题。
    missing = [q for q in questions if str(q.get("id")) not in matches]
    numeric_keys = [k for k, _v in pending
                    if re.fullmatch(r"\d+(?:[.．]\d+)*", str(k or ""))]
    if pending and len(pending) == len(missing) \
            and len(numeric_keys) == len(pending):
        unmatched = [k for k, _v in pending]
        warnings.append(
            "有 %d 个答案键没有匹配到题号（%s），已按题目顺序一一对应；"
            "扫描件可用 analyze --numbers 指定真实题号"
            % (len(pending), "、".join(unmatched[:8])))
        for (key, value), q in zip(pending, missing):
            if value is None:
                continue
            text = value if isinstance(value, str) else str(value)
            text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
            if text:
                matches[str(q.get("id"))] = text
    else:
        for key, _value in pending:
            unmatched.append(key)
        if unmatched:
            warnings.append("有 %d 个答案键没有匹配到题目：%s（可用题号或 Q1 形式）"
                            % (len(unmatched), "、".join(unmatched[:10])))
    missing = [str(q.get("id")) for q in questions
               if str(q.get("id")) not in matches]
    return matches, warnings, unmatched, missing


# ======================================================================
# 排版：默认 12.4pt，超过答题区才缩到 8pt
# ======================================================================
# 分数（\frac）是上下结构：渲染器实测 12.4pt 时纯文字一行 22.9pt、
# 带分数一行 31.3pt，即每个「含分数的行」多出约 0.68 个字高；
# 根号与上下标实测不增高（22.8 / 22.5pt）。
FRAC_EXTRA_EM = 0.68


def _char_units(ch):
    """单个字符的宽度（单位：一个汉字宽）。"""
    if "\u4e00" <= ch <= "\u9fff":
        return 1.0
    if ch.isspace():
        return 0.3
    if ord(ch) < 128:
        return 0.56
    if 0x0370 <= ord(ch) <= 0x03FF:      # 希腊字母
        return 0.62
    return 0.85


def _plain_units(s):
    """不含公式的纯文字宽度（汉字宽）。"""
    return sum(_char_units(ch) for ch in s or "")


def _take_group(s, i):
    """从 s[i] 取一个 {…} 组（没有花括号就取一个字符），返回 (内容, 新下标)。"""
    if i >= len(s):
        return "", i
    if s[i] != "{":
        return s[i], i + 1
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1:j], j + 1
    return s[i + 1:], len(s)


def _math_units(expr):
    """估算 $...$ 里公式的宽度（汉字宽）：分数取分子/分母较宽者，上下标不占宽。"""
    s = expr or ""
    total, i = 0.0, 0
    while i < len(s):
        if s.startswith(("\\frac", "\\dfrac", "\\tfrac"), i):
            j = i + (6 if s.startswith(("\\dfrac", "\\tfrac"), i) else 5)
            a, j = _take_group(s, j)
            b, j = _take_group(s, j)
            total += max(_math_units(a), _math_units(b)) + 0.30
            i = j
        elif s.startswith("\\sqrt", i):
            j = i + 5
            if j < len(s) and s[j] == "[":
                k = s.find("]", j)
                j = (k + 1) if k > 0 else j
            a, j = _take_group(s, j)
            total += _math_units(a) + 0.42
            i = j
        elif s[i] in "^_":
            _arg, j = _take_group(s, i + 1)
            total += 0.04                 # 上下标叠在字上，几乎不额外占宽
            i = j
        elif s[i] == "\\":
            j = i + 1
            while j < len(s) and s[j].isalpha():
                j += 1
            total += 0.62                 # \times \cdot 这类操作符
            i = j
        elif s[i] == "{":
            a, i = _take_group(s, i)
            total += _math_units(a)
        else:
            total += _char_units(s[i])
            i += 1
    return max(0.2, total)


def _em_units(text):
    """估算文字宽度（单位：一个汉字宽），$...$ 按公式排版结果算，不按字符数。

    旧版把 ``\\frac{220}{10}`` 当 16 个半角字符（≈7.2 字宽），实际只有 2 字多，
    结果行数估多、字号被无谓缩小。分数的高度由 FRAC_EXTRA_EM 单独补。
    """
    s = str(text or "")
    total, i = 0.0, 0
    while i < len(s):
        j = s.find("$", i)
        if j < 0:
            total += _plain_units(s[i:])
            break
        total += _plain_units(s[i:j])
        k = s.find("$", j + 1)
        if k < 0:                          # 不配对的 $ 当普通字符
            total += _plain_units(s[j:])
            break
        total += _math_units(s[j + 1:k])
        i = k + 1
    return max(0.1, total)


def _frac_lines(text):
    """含 \\frac 的行数（同一行里几个分数一样高，只算一次）。"""
    n = 0
    for line in str(text or "").replace("\n", HARD_BREAK).split(HARD_BREAK):
        if "\\frac" in line or "\\dfrac" in line or "\\tfrac" in line:
            n += 1
    return n


def estimate_line_count(text, width_pt, size_pt):
    """估算 text 在给定宽度/字号下的行数。

    基准：12.4pt 时一行约能写 ``rect宽 / 13.3`` 个汉字（来自渲染器实测；
    13.3pt 是 12.4pt 手写汉字的平均步进）。字号变化按比例换算。
    语义硬换行标记（HARD_BREAK）与 '\\n' 一样，都算一次强制分行。
    """
    if not text:
        return 0
    chars_per_line = max(1.0, width_pt / (13.3 * size_pt / 12.4))
    total = 0
    segmented = str(text).replace(HARD_BREAK, "\n")
    for para in segmented.split("\n"):
        para = para.strip()
        if not para:
            continue
        total += max(1, int(math.ceil(_em_units(para) / chars_per_line)))
    return max(1, total)


_MEASURE_HANDS = {}


def _measure_hand(size_pt):
    """估高用的 Hand（按字号缓存）。

    为什么不用字符数估：分式/根号会把行撑高，而且撑高量与字号成正比，
    `字数 × 行距 + 固定补偿` 永远对不上——实测 20pt 时低估 50pt（一整行），
    上一题的答案就会压到下一题上。直接用渲染器自己的排版算法算，误差只剩
    几像素（并且偏高，正好当余量）。
    """
    key = round(float(size_pt), 2)
    if key in _MEASURE_HANDS:
        return _MEASURE_HANDS[key]
    hand = None
    try:
        import symbols
        # jitter 必须与真正渲染时一致（默认 1.0）：换行宽度按
        # (1 + _JITTER_INFLATE*jitter) 预留，用 0 会排出更长的行、少一行，
        # 估出来的高度反而偏小。
        hand = symbols.make_han_hand(size_pt=key, seed=20261118, jitter=1.0)
    except Exception:
        hand = None
    _MEASURE_HANDS[key] = hand
    return hand


def estimate_answer_height(text, width_pt, size_pt, line_pt=None):
    """答案渲染后的高度（pt）：走渲染器同一套换行/行距，不再靠字数猜。

    旧实现按 ``行数 × 行距 + 分数补偿`` 估算，误差随字号放大（20pt 时少算
    50pt，正好一行），是「两段答案叠在一起」的根因。现在直接调
    `hwrender.Hand.measure_block`；拿不到字库时退回旧算法。
    """
    size = max(1.0, float(size_pt))
    hand = _measure_hand(size)
    if hand is not None:
        try:
            _w, h_pt, _n = hand.measure_block(str(text or ""),
                                              width_pt=width_pt,
                                              size_pt=size, line_pt=line_pt)
            return float(h_pt)
        except Exception:
            pass
    lp = float(line_pt) if line_pt else round(size * LINE_RATIO, 1)
    lines = estimate_line_count(text, width_pt, size)
    extra = min(_frac_lines(text), lines) * FRAC_EXTRA_EM * size
    return lines * lp + extra


def fit_answer_size(text, answer_rect, start_size=SIZE_DEFAULT_PT):
    """为一段答案在 answer_rect 里挑可用字号。

    默认 12.4pt（与渲染器默认一致，不因答题区大就放大）；只有估算高度超过
    答题区才每次降 0.5pt，最低 8pt。``start_size`` 允许调用方从别的字号开始
    （空白答题页会用用户为该题设的字号）。返回
    ``(size_pt, line_pt, lines, need_pt, fits)``；仍放不下时 fits=False。
    """
    rect = _rect_key(answer_rect)
    avail_h = max(1.0, rect[3] - rect[1])
    width = max(1.0, rect[2] - rect[0])
    try:
        size = max(SIZE_MIN_PT, min(30.0, float(start_size)))
    except (TypeError, ValueError):
        size = SIZE_DEFAULT_PT
    while size >= SIZE_MIN_PT - 1e-9:
        line_pt = round(size * LINE_RATIO, 1)
        lines = estimate_line_count(text, width, size)
        need = estimate_answer_height(text, width, size, line_pt)
        if need <= avail_h + FIT_TOLERANCE_PT:
            return round(size, 1), line_pt, lines, need, True
        size -= SIZE_STEP_PT
    size = SIZE_MIN_PT
    line_pt = round(size * LINE_RATIO, 1)
    lines = estimate_line_count(text, width, size)
    need = estimate_answer_height(text, width, size, line_pt)
    return size, line_pt, lines, need, False


def _charset_warnings(matches):
    """检查答案用字是否都在当前字库内，返回 warning 列表。

    字表跟着字体走（用户补字/换库后自动变），所以不写死「500 + 204」，
    汉字判定也走 ai_prompt.is_han_char（含扩展 A 等码段）。
    """
    try:
        import ai_prompt
        han, sym = ai_prompt.allowed_charset()
        is_han = ai_prompt.is_han_char
    except Exception:
        return []
    out = []
    for qid, text in matches.items():
        missing_han = sorted({c for c in text if is_han(c) and c not in han})
        outside = sorted({c for c in text
                          if (not c.isspace()) and (not is_han(c))
                          and c not in sym})
        if missing_han:
            out.append("题目 %s 答案含 %d 个字库外汉字（将用回退字体）：%s"
                       % (qid, len(missing_han), "".join(missing_han[:20])))
        if outside:
            out.append("题目 %s 答案含 %d 个字库外字符（可能渲染异常）：%s"
                       % (qid, len(outside),
                          "".join(repr(c) for c in outside[:10])))
    return out


# ======================================================================
# render_answers
# ======================================================================

# ---------------------------------------------------------------- 答案人性化
# 人手写作业里除以很少写斜杠：要么写分数（分子/分母上下叠），要么写 ÷。
# AI 常输出 "220/10"，直接按普通字符写出来就是斜杠，很“机器”。
# 这里在渲染前把 ASCII 斜杠换成 LaTeX 分数式（走公式层，渲染成横线上下结构）。
# 2026-11-26 修正（用户实测反馈）：
#   1. U^2/R 曾被拆成 U^\frac{2}{R}：幂被吞进分母，式子语义变了
#      → 现在把「底数 + 幂/下标」整体当分子；
#   2. 链式除法 380/1.732/10 只转了第一段，后面留一个裸斜杠
#      → 现在整条折成一个分数（a/(b×c)，与左结合的 a/b/c 数值等价）；
#   3. cosφ、√3 这类多字符分母一律不转 → 现在支持字母段、希腊字母、根式；
#      单位比值（kW/h、m/s、220V/10A）仍原样保留，不当分数；
#   4. 裸根号「√3」只有一根棍，被开方数没被包进根号
#      → 单独做一遍 √ 归一化，写成 $\sqrt{...}$。

# 单位比值不是分数：kW/h、m/s 这类写法要原样留着，
# 写成 $\frac{kW}{h}$ 反而像两个物理量相除。
_UNIT_TOKENS = frozenset((
    "v", "kv", "mv", "a", "ma", "ua", "w", "kw", "mw", "va", "kva", "mva",
    "var", "kvar", "hz", "khz", "mhz", "f", "mf", "uf", "pf", "h", "mh",
    "s", "ms", "us", "min", "km", "m", "cm", "mm", "kg", "g", "kwh",
    "db", "rpm",
))

# 一个「算式操作数」：数字、字母段（可含希腊字母）、已包好的 $...$ 公式片段、
# 根式；后面可再跟一个幂/下标（U^2、U^{2}、I_1）。
_EXP = r"(?:[\^_](?:\{[^{}]*\}|[A-Za-z0-9]))?"
_OPERAND = (
    r"(?:"
    r"\$[^$]+\$"                                # 已包好的公式片段（√ 归一化后）
    r"|\\sqrt\s*(?:\{[^{}]*\}|[0-9A-Za-z.]+)"    # 公式里的根式：\sqrt{3} / \sqrt3
    r"|√\s*(?:\([^()]*\)|[0-9A-Za-z.]+)"        # 兜底：还没归一化的裸根号
    r"|\([^()]*(?:\([^()]*\)[^()]*)*\)"         # 括号组：P1/(Ucosφ)、400/(220×0.5)
    r"|-?\d+(?:\.\d+)?"                         # 数字：220 / 1.732 / -3
    r"|[A-Za-zΑ-Ωα-ω][A-Za-z0-9Α-Ωα-ω]*"        # 字母段：U / cosφ / ωL / j6
    r")" + _EXP
)

# 分子前面不能是 LaTeX 反斜杠、单词字符、小数点、幂次符（别切进 \frac、W/h、U^2）；
# 分母后面同理，别把 220V、10A 这种「数字+单位」切一半。
_DIVISION_RE = re.compile(
    r"(?<![\\A-Za-z0-9_.\^_])(" + _OPERAND + r")"
    r"((?:\s*/\s*" + _OPERAND + r")+)"
    r"(?![A-Za-z0-9_])"
)

# 裸根号：√3 / √(a+b) / √1.732。括号不配对时不动，宁可原样也别把式子搞错。
_RADICAL_RE = re.compile(
    r"(?<![\\A-Za-z0-9_])√\s*(?:\(([^()]*)\)|([0-9A-Za-z.]+))")


def _in_math(text, pos):
    """pos 之前有奇数个 $ 就是处在 $...$ 里。"""
    return text.count("$", 0, pos) % 2 == 1


def _strip_math(tok):
    """$...$ 片段去掉定界符；非公式片段原样返回。"""
    t = tok.strip()
    if len(t) >= 2 and t.startswith("$") and t.endswith("$"):
        return t[1:-1]
    return t


def _operand_base(tok):
    """操作数的「底」：去掉 $、负号、幂次，用来判断是不是单位。"""
    s = _strip_math(tok).lstrip("-")
    s = re.split(r"[\^_]", s)[0]
    return s.strip().lower()


def _is_unit_operand(tok):
    return _operand_base(tok) in _UNIT_TOKENS


def _wrap_radicals(text):
    """裸根号 → 公式根式：√3 写成 $\\sqrt{3}$（已在 $...$ 里就不加定界符）。"""
    def repl(m):
        inner = (m.group(1) if m.group(1) is not None
                 else (m.group(2) or "")).strip()
        if not inner:
            return m.group(0)
        body = r"\sqrt{%s}" % inner
        return body if _in_math(text, m.start()) else "$" + body + "$"
    return _RADICAL_RE.sub(repl, text)


def _plain_operand(tok):
    """分式里的操作数：去掉 $ 定界符、去掉把整个操作数包起来的一层括号。

    分式线本身就有分组作用，"400/(220×0.5)" 写成 \\frac{400}{220×0.5} 更像手写；
    括号里如果有 $\\sqrt{3}$（前面的根号归一化加的），要一并去掉 $，
    否则会嵌出 "$\\frac{400}{$\\sqrt{3}$...}$" 这种坏公式。
    """
    s = _strip_math(tok).replace("$", "").strip()
    if len(s) >= 3 and s[0] == "(" and s[-1] == ")":
        depth = 0
        for i, c in enumerate(s):
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0 and i != len(s) - 1:
                    return s          # 外层没包住整个操作数
        return s[1:-1].strip()
    return s


def _fold_divisions(text):
    """a/b → 分数式；a/b/c → a/(b×c)（与左结合的结果数值等价）。"""
    def repl(m):
        num = m.group(1)
        dens = re.findall(_OPERAND, m.group(2))
        if not dens:
            return m.group(0)
        if _is_unit_operand(num) and all(_is_unit_operand(d) for d in dens):
            return m.group(0)                    # 单位比值不当分数
        body = r"\frac{%s}{%s}" % (
            _plain_operand(num),
            r"\times ".join(_plain_operand(d) for d in dens))
        return body if _in_math(text, m.start()) else "$" + body + "$"
    return _DIVISION_RE.sub(repl, text)


def humanize_answer(text):
    """把答案里的 ASCII 除号斜杠和裸根号改成手写习惯。

    · ``a/b`` → ``$\\frac{a}{b}$``；已在 ``$...$`` 里的只换成 ``\\frac{}{}``，
      不重复加定界符；``a/b/c`` 折成一个分数，分母用 ``\\times`` 连起来；
    · ``√3`` → ``$\\sqrt{3}$``，``√(a+b)`` → ``$\\sqrt{a+b}$``；
    · 单位比值（kW/h、m/s、220V/10A）和 ``kvar/h`` 这类写法原样保留。
    """
    s = text or ""
    if "/" not in s and "√" not in s:
        return s
    return _fold_divisions(_wrap_radicals(s))


# ---------------------------------------------------------------- 语义断行
# 真人写作业不会把好几句、好几步计算连成一行：写完一句（句号/问号/叹号/
# 分号）或算完一步（"= 22A" 之后转回汉字叙述）就会另起一行。
# 之前 render_answers 只按列宽做最优换行，于是出现了「一句接一句挤满一整行、
# 句号后继续写下一句」的机器感排版（用户反馈原话）。
_SENT_END = set("。！？；…!?;")                 # 句末标点：写完必换行
_CJK_PUNCT = set("，、：》）」』”’《「『“‘—～·,:")  # 句内标点：不断行，免得行首挂个逗号
_CALC_UNITS = set("+-×÷/.%^()|√∑Ω℃")        # 算式里允许出现的符号


def _is_han(ch):
    return "\u4e00" <= ch <= "\u9fff"


def semantic_breaks(text):
    """把答案按「句子写完 / 算式算完」切成硬换行行（用 HARD_BREAK 连接）。

    保守规则（宁可少断，也别把句子切碎）：
      1. 句末标点（。！？；… 及半角 !?;）之后断行，连续句末标点一并收进本行；
      2. 只有出现 ``=`` 才算进入「计算式」：结果片段里已有数字/字母，
         且紧接着转回汉字叙述时，才在结果后断行。
         于是 "已知 U=220V，求 I" 不会被逗号劈开、"，求 I" 也不会单独成行；
         而 "I=U/|Z|=...=22A 故…" 会在 22A 后另起一行。
      3. 原文里的 ``\\n`` 也算一次换行（AI 常按步骤分行输出）；
      4. ``$...$`` 公式内部的标点/等号不参与判断，分式不会被从中间劈开；
      5. 去掉空行与首尾空白，连续换行合并为一个。
    """
    if not text:
        return ""
    s = str(text).replace("\r\n", "\n").replace("\r", "\n")
    out, cur = [], []
    in_math = False
    calc = False          # 正处于 "= ..." 的计算结果片段
    calc_body = False     # 片段里已出现数字/字母（避免 "=" 后直接跟汉字就断）
    i, n = 0, len(s)

    def flush():
        line = "".join(cur).strip()
        if line:
            out.append(line)
        cur.clear()

    while i < n:
        ch = s[i]
        if ch == "$":
            # 公式本身就是算式的一部分，收尾时把它算作「已有结果」
            if in_math and calc:
                calc_body = True
            in_math = not in_math
            cur.append(ch)
            i += 1
            continue
        if in_math:
            cur.append(ch)
            i += 1
            continue
        if ch in ("\n", HARD_BREAK):
            flush()
            calc = calc_body = False
            i += 1
            continue
        if ch in _SENT_END:
            cur.append(ch)
            i += 1
            while i < n and s[i] in _SENT_END:
                cur.append(s[i])
                i += 1
            flush()
            calc = calc_body = False
            continue
        if ch == "=":
            calc, calc_body = True, False
            cur.append(ch)
            i += 1
            continue
        if calc:
            if _is_han(ch):
                if calc_body:
                    # 算式算完，转回汉字叙述 → 另起一行
                    flush()
                    calc = calc_body = False
                    cur.append(ch)
                    i += 1
                    continue
                calc = False
            elif ch in _CJK_PUNCT:
                # 标点结束算式，但不在标点前断行（别排出以标点开头的行）
                calc = False
                cur.append(ch)
                i += 1
                continue
            else:
                if ch.isalnum() or ch in _CALC_UNITS:
                    calc_body = True
                cur.append(ch)
                i += 1
                continue
        cur.append(ch)
        i += 1
    flush()
    return HARD_BREAK.join(x for x in out if x.strip())


# ---------------------------------------------------------------- 空白答题页
def _resolve_answer_mode(spec, answer_mode=None):
    """归一化答案位置：inline（原页答题区）/ blank（新插空白页）。"""
    mode = str(answer_mode or (spec or {}).get("answer_mode")
               or ANSWER_MODE_INLINE).strip().lower()
    return ANSWER_MODE_BLANK if mode == ANSWER_MODE_BLANK else ANSWER_MODE_INLINE


def _answer_heading(number):
    """空白页上每题开头的题号前缀：1. / 4.2.1 / （3） 原样保留。"""
    num = str(number or "").strip()
    if not num:
        return ""
    if re.search(r"[.．、)）]$", num):
        return num + "  "
    if re.search(r"[.．]\d", num):       # 4.2.1 这种多级编号不再加句点
        return num + "  "
    return num + ".  "


def _split_answer_chunks(text, width_pt, max_lines, size_pt):
    """按语义硬换行把长答案切成「一页装得下」的块。

    只按 HARD_BREAK 切，不会把一句话劈开；最后一块仍可能超长，交给
    fit_answer_size 缩字号兜底。
    """
    pieces = [p for p in str(text).split(HARD_BREAK)]
    out, cur = [], []
    for piece in pieces:
        trial = HARD_BREAK.join(cur + [piece]) if cur else piece
        if cur and estimate_answer_height(
                trial, width_pt, size_pt) > max_lines * round(
                    size_pt * LINE_RATIO, 1):
            out.append(HARD_BREAK.join(cur))
            cur = [piece]
        else:
            cur.append(piece)
    if cur:
        out.append(HARD_BREAK.join(cur))
    return [x for x in out if x.strip()] or [str(text)]


def _compose_blank_answer_pages(spec, matches, pm, seed, jitter,
                                warnings, coverage, preferred_sizes=None):
    """把答案排到「新插入的空白页」上（每页作业后插页）。

    返回 (pages_out, page_indices)：page_indices 是答案所属的**源页下标**，
    导出时据此把空白页插到对应作业页后面；一题太长时会自动续页。
    preferred_sizes 是 {题号: pt}：GUI 里为该题改过字号时，空白页间距也
    按该字号估算，避免大字号答案互相压住。
    """
    preferred_sizes = preferred_sizes or {}
    pages_out, page_indices = [], []
    running = 0
    for pg_i, pg in enumerate(spec.get("pages") or []):
        qs = [q for q in (pg.get("questions") or [])
              if str(q.get("id")) in matches]
        if not qs:
            continue
        page_w = float(pg.get("width_pt") or 595.0)
        page_h = float(pg.get("height_pt") or 842.0)
        left = BLANK_ANSWER_MARGIN_PT
        right = max(left + 120.0, page_w - BLANK_ANSWER_MARGIN_PT)
        width = right - left
        bottom_limit = page_h - BLANK_ANSWER_BOTTOM_PT
        page = pm.Page(width_pt=page_w, height_pt=page_h)
        top = BLANK_ANSWER_TOP_PT
        for q in qs:
            qid = str(q.get("id"))
            try:
                base_size = _clamp(float(preferred_sizes.get(qid)
                                         or SIZE_DEFAULT_PT),
                                   SIZE_MIN_PT, 30.0)
            except (TypeError, ValueError):
                base_size = SIZE_DEFAULT_PT
            line_base = round(base_size * LINE_RATIO, 1)
            max_lines = max(1, int((bottom_limit - BLANK_ANSWER_TOP_PT)
                                   // line_base))
            text = semantic_breaks(humanize_answer(matches[qid]))
            chunks = _split_answer_chunks(
                _answer_heading(q.get("number")) + text,
                width, max_lines, base_size) or [text]
            total_lines, total_need = 0, 0.0
            min_size = base_size
            first_avail = None
            all_fits = True
            line_pt = round(base_size * LINE_RATIO, 1)
            for ci, chunk in enumerate(chunks):
                gap = BLANK_ANSWER_GAP_PT if (ci == 0 and page.paragraphs) else 0.0
                need_default = estimate_answer_height(chunk, width, base_size)
                if page.paragraphs and top + gap + need_default > bottom_limit:
                    pages_out.append(page)
                    page_indices.append(pg_i)
                    page = pm.Page(width_pt=page_w, height_pt=page_h)
                    top = BLANK_ANSWER_TOP_PT
                    gap = 0.0
                top += gap
                avail = max(1.0, bottom_limit - top)
                if first_avail is None:
                    first_avail = avail
                size, line_pt, lines, need, fits = fit_answer_size(
                    chunk, [left, top, right, bottom_limit],
                    start_size=base_size)
                min_size = min(min_size, size)
                all_fits = all_fits and bool(fits)
                if size < SIZE_DEFAULT_PT - 1e-9:
                    if qid not in coverage["scaled_ids"]:
                        coverage["scaled_ids"].append(qid)
                if not fits:
                    warnings.append(
                        "题目 %s 的答案超过一整页空白答题页（8pt 仍放不下），"
                        "已照常书写，可能写出版心" % qid)
                # 留 8pt 余量，避免实际笔迹比估算高一点就压到下一题
                block_h = min(max(need + 8.0, 12.0), avail)
                rect = [left, top, right, round(top + block_h, 1)]
                para = pm.Paragraph(
                    text=chunk, rect=rect, size_pt=size, line_pt=line_pt,
                    align="left", jitter=jitter,
                    seed=int(seed) + running * 17,
                    ink=tuple(DEFAULT_INK))
                try:
                    para._qid = qid
                except Exception:
                    pass
                page.add_paragraph(para)
                running += 1
                total_lines += lines
                total_need += need
                top = rect[3] + (0.0 if ci < len(chunks) - 1
                                 else BLANK_ANSWER_GAP_PT)
            coverage["answered_ids"].append(qid)
            coverage["sizes"][qid] = {
                "size_pt": round(min_size, 1), "line_pt": line_pt,
                "lines_est": total_lines, "need_pt": round(total_need, 1),
                "avail_pt": round(first_avail if first_avail is not None
                                   else (bottom_limit - BLANK_ANSWER_TOP_PT), 1),
                "fits": all_fits, "page": "blank",
                "chunks": len(chunks),
            }
        pages_out.append(page)
        page_indices.append(pg_i)
    return pages_out, page_indices


def _export_blank_answer_pdf(pm, spec, pages_out, page_indices, out_pdf):
    """按「作业页 → 该页的空白答题页」顺序拼最终 PDF（原页原样保留）。

    返回 {源页下标: [输出页下标, ...]}，供 GUI 生成后定位预览页。
    """
    import pymupdf as fitz
    from collections import defaultdict

    sheets = defaultdict(list)
    for page, src_i in zip(pages_out, page_indices):
        sheets[int(src_i)].append(page)

    source = str(spec.get("source") or "")
    kind = spec.get("kind") or ("pdf" if source.lower().endswith(".pdf")
                                else "image")
    out = fitz.open()
    mapping = {}

    def add_sheet(sheet):
        pg = out.new_page(width=float(sheet.width_pt),
                          height=float(sheet.height_pt))
        layer = pm.handwriting_layer(sheet)
        buf = io.BytesIO()
        layer.save(buf, format="PNG")
        pg.insert_image(pg.rect, stream=buf.getvalue(), overlay=True,
                        keep_proportion=False)

    try:
        if kind == "pdf" and source and os.path.exists(source):
            src = fitz.open(source)
            try:
                for i in range(len(src)):
                    out.insert_pdf(src, from_page=i, to_page=i)
                    if sheets.get(i):
                        mapping[i] = []
                        for sheet in sheets[i]:
                            mapping[i].append(len(out))
                            add_sheet(sheet)
            finally:
                src.close()
        else:
            for i, pg_spec in enumerate(spec.get("pages") or []):
                if i == 0 and sheets.get(0):
                    base = pm.Page(
                        width_pt=float(pg_spec.get("width_pt") or 595.0),
                        height_pt=float(pg_spec.get("height_pt") or 842.0),
                        bg_kind="image", bg_path=source)
                    _, base_img = pm.render_page(base)
                    out_pg = out.new_page(width=base.width_pt,
                                          height=base.height_pt)
                    buf = io.BytesIO()
                    base_img.convert("RGB").save(buf, format="PNG")
                    out_pg.insert_image(out_pg.rect, stream=buf.getvalue(),
                                        overlay=False, keep_proportion=False)
                if sheets.get(i):
                    mapping[i] = []
                    for sheet in sheets[i]:
                        mapping[i].append(len(out))
                        add_sheet(sheet)
        out.save(out_pdf, deflate=True, garbage=3)
    finally:
        out.close()
    return mapping


def _compose_answer_pages(spec, answers, page_model=None,
                          seed=20261118, jitter=1.0, answer_mode=None,
                          preferred_sizes=None):
    """把答案排进 spec 的答题区，只构造 Page 对象（不落盘、不渲染位图）。

    这是 render_answers / preview_pages 共用的排版内核：GUI 的实时预览
    与 CLI / HTTP API 的导出因此走完全相同的字号、人性化与笔迹逻辑。

    answer_mode=blank 时不排进原页答题区，而是在每题所属作业页后面新插
    空白页写答案（见 _compose_blank_answer_pages）。

    返回 (pm, pages_out, page_indices, warnings, coverage)：
      pages_out   与 page_indices 一一对应（page_indices 是 spec.pages 下标，
                  即 GUI 的当前页号；Page.bg_page 仍存源 PDF 页号）；
      warnings    未去重的原始告警，调用方负责 _dedupe。
    """
    if not isinstance(spec, dict) or not isinstance(spec.get("pages"), list):
        raise ValueError("spec 结构不合法：需要含 pages 数组的作业页 spec")
    pm = page_model
    if pm is None:
        import page_model as pm

    questions = [q for _pg, q in _iter_questions(spec)]
    if not questions:
        raise ValueError("spec 里没有题目，无法作答（先运行 analyze）")
    matches, warnings, unmatched, missing = normalize_answers(answers, questions)
    del unmatched
    if not matches:
        ids = "、".join(str(q.get("id")) for q in questions[:10])
        raise ValueError("答案没有匹配到任何题目（可用题号 1/2… 或 id %s）"
                         % ids)
    for qid in missing:
        warnings.append("题目 %s 没有答案，答题区留空" % qid)
    warnings.extend(_charset_warnings(matches))

    source = spec.get("source") or ""
    kind = spec.get("kind") or ("pdf" if source.lower().endswith(".pdf")
                                else "image")
    pages_out = []
    page_indices = []
    coverage = {
        "spec_version": spec.get("version") or SPEC_VERSION,
        "questions": len(questions),
        "answered": len(matches),
        "coverage": round(len(matches) / float(len(questions)), 4)
        if questions else 0.0,
        "answered_ids": [],
        "missing_ids": missing,
        "sizes": {},
        "scaled_ids": [],
        "warnings": [],
    }
    mode = _resolve_answer_mode(spec, answer_mode)
    coverage["answer_mode"] = mode
    if mode == ANSWER_MODE_BLANK:
        pages_out, page_indices = _compose_blank_answer_pages(
            spec, matches, pm, seed, jitter, warnings, coverage,
            preferred_sizes=preferred_sizes)
        if not pages_out:
            raise ValueError("答案没有匹配到任何页面")
        return pm, pages_out, page_indices, warnings, coverage

    for pg_i, pg in enumerate(spec.get("pages") or []):
        page_questions = [q for q in (pg.get("questions") or [])
                          if str(q.get("id")) in matches]
        if not page_questions:
            continue
        page = pm.Page(width_pt=float(pg.get("width_pt") or 595.0),
                       height_pt=float(pg.get("height_pt") or 842.0))
        if kind == "pdf" and source:
            page.source_pdf = source
            page.bg_kind = "pdf"
            page.bg_path = source
            page.bg_page = int(pg.get("page") or 0)
            page.bg_dpi = 200
        elif source:
            page.bg_kind = "image"
            page.bg_path = source
            page.bg_page = 0
        for i, q in enumerate(page_questions):
            qid = str(q.get("id"))
            # 先人性化（220/10 → 分式），再按「句末 / 算式算完」切硬换行，
            # 让每句话、每步计算各占一行，而不是挤成一整行。
            text = semantic_breaks(humanize_answer(matches[qid]))
            rect = _rect_key(q.get("answer_rect") or [56, 120, 539, 300])
            size, line_pt, lines, need, fits = fit_answer_size(text, rect)
            if not fits:
                warnings.append(
                    "题目 %s 答案过长：最小字号 %.1fpt 仍需 %d 行≈%.0fpt，"
                    "答题区只有 %.0fpt；已不截断照常书写，可能超出答题区"
                    % (qid, SIZE_MIN_PT, lines, need, rect[3] - rect[1]))
            if size < SIZE_DEFAULT_PT - 1e-9:
                coverage["scaled_ids"].append(qid)
            # 从 answer_rect 顶部开始写、宽度≈rect 宽（只留 0.5pt 防裁边）
            para_rect = [round(rect[0] + 0.5, 1), round(rect[1] + 0.5, 1),
                         round(rect[2] - 0.5, 1), round(rect[3] - 1.0, 1)]
            para = pm.Paragraph(
                text=text, rect=para_rect, size_pt=size, line_pt=line_pt,
                align="left", jitter=jitter, seed=int(seed) + i * 17,
                ink=tuple(DEFAULT_INK))
            try:
                para._qid = qid        # GUI 的 page_model 代理按题目注入覆盖
            except Exception:
                pass
            page.add_paragraph(para)
            coverage["answered_ids"].append(qid)
            coverage["sizes"][qid] = {
                "size_pt": size, "line_pt": line_pt, "lines_est": lines,
                "need_pt": round(need, 1),
                "avail_pt": round(rect[3] - rect[1], 1),
                "fits": bool(fits),
            }
        pages_out.append(page)
        page_indices.append(int(pg_i))   # spec.pages 下标 = GUI 的 self.page

    if not pages_out:
        raise ValueError("答案没有匹配到任何页面")
    return pm, pages_out, page_indices, warnings, coverage


def render_answers(spec, answers, out_pdf=None, out_png=None, page_model=None,
                   seed=20261118, jitter=1.0, answer_mode=None,
                   preferred_sizes=None):
    """把答案手写排进 spec 的答题区并导出 PDF / PNG。

    参数
    ----
    spec       : analyze() 的返回值（或 load_spec 读到的 layout）
    answers    : {题号 或 id: 文本} 或 [{id/number, text}]
    out_pdf    : 输出 PDF 路径（None 则只排版不落盘）
    out_png    : 输出 PNG 预览路径（多页时只出第一个有答案的页）
    page_model : 可注入 page_model 模块（默认按同目录导入）
    seed/jitter: 手写笔迹参数，传给 symbols.make_han_hand
    answer_mode: "inline"（默认，写回原页答题区）/"blank"（每页作业后插
                 空白页写答案）；也读 spec["answer_mode"]。
    preferred_sizes: {题号: pt}，用户为某题指定的字号（空白页排间距用）。

    返回 (pdf_path, warnings, coverage_summary)；答案一个都没匹配到题目时
    抛 ValueError（CLI/API 转成明确错误）。
    """
    mode = _resolve_answer_mode(spec, answer_mode)
    pm, pages_out, page_indices, warnings, coverage = _compose_answer_pages(
        spec, answers, page_model=page_model, seed=seed, jitter=jitter,
        answer_mode=mode, preferred_sizes=preferred_sizes)
    pdf_path = None
    if out_pdf:
        out_dir = os.path.dirname(os.path.abspath(out_pdf))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        if mode == ANSWER_MODE_BLANK:
            coverage["blank_pages"] = _export_blank_answer_pdf(
                pm, spec, pages_out, page_indices, out_pdf)
        else:
            page_arg = pages_out if len(pages_out) > 1 else pages_out[0]
            pm.export_pdf(page_arg, out_pdf)
        pdf_path = out_pdf
        for p in pages_out:
            warnings.extend(list(getattr(p, "_warn", [])))
    if out_png:
        out_dir = os.path.dirname(os.path.abspath(out_png))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        _, img = pm.render_page(pages_out[0])
        warnings.extend(list(pages_out[0]._warn))
        img.convert("RGB").save(out_png)
        if len(pages_out) > 1:
            warnings.append("PNG 预览只输出第一页（共 %d 页有答案），"
                            "完整内容见 PDF" % len(pages_out))
    warnings = _dedupe([w for w in warnings if w])
    coverage["warnings"] = warnings
    return pdf_path, warnings, coverage


def preview_pages(spec, answers, page_model=None, only_page=None,
                  seed=20261118, jitter=1.0, answer_mode=None,
                  preferred_sizes=None):
    """「实时预览」内核：把答案手写叠加到作业页背景，纯内存、不写任何文件。

    与 render_answers 共用 _compose_answer_pages 排版，再对需要的页调用
    page_model.render_page() 得到整页位图（原 PDF 页作底图 + 手写作答层），
    因此界面上看到的效果与最终导出的 PDF 完全一致，但完全不落盘。

    参数
    ----
    only_page : 只渲染这个页下标（None 表示渲染所有有答案的页）；
                答案覆盖率等统计仍按全量计算。
    answer_mode : "blank" 时返回的图是「该页作业后面新插的空白答题页」，
                同一源页有多张答题页时只返回第一张（预览用）。

    返回 (images, warnings, coverage)，images 为 {页下标: PIL.Image(RGB)}。
    """
    mode = _resolve_answer_mode(spec, answer_mode)
    pm, pages_out, page_indices, warnings, coverage = _compose_answer_pages(
        spec, answers, page_model=page_model, seed=seed, jitter=jitter,
        answer_mode=mode, preferred_sizes=preferred_sizes)
    images = {}
    for page_idx, page in zip(page_indices, pages_out):
        if only_page is not None and int(page_idx) != int(only_page):
            continue
        if mode == ANSWER_MODE_BLANK and int(page_idx) in images:
            continue
        _, img = pm.render_page(page)
        warnings.extend(list(getattr(page, "_warn", [])))
        images[int(page_idx)] = img.convert("RGB")
    warnings = _dedupe([w for w in warnings if w])
    coverage["warnings"] = warnings
    return images, warnings, coverage


# ======================================================================
# spec 读写
# ======================================================================
def load_spec(path):
    """读 layout.json；兼容 HTTP /worksheet/analyze 的 {ok, spec} 外层。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("layout 路径为空")
    p = os.path.abspath(os.path.expanduser(os.path.expandvars(path.strip())))
    if not os.path.exists(p):
        raise FileNotFoundError("layout 文件不存在：%s" % p)
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            obj = json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError("layout 不是合法 JSON：%s（%s）" % (p, e))
    if isinstance(obj, dict) and isinstance(obj.get("spec"), dict) \
            and "pages" not in obj:
        obj = obj["spec"]
    if not isinstance(obj, dict) or not isinstance(obj.get("pages"), list):
        raise ValueError("layout 结构不合法：需要含 pages 数组的作业页 spec：%s" % p)
    return obj


def save_spec(spec, path):
    """把 spec 写成 UTF-8 JSON（自动建目录），返回路径。"""
    if not isinstance(spec, dict):
        raise ValueError("spec 必须是 JSON 对象")
    p = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    out_dir = os.path.dirname(p)
    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    return p


__all__ = [
    "SPEC_VERSION", "QUESTION_RE", "analyze", "render_answers",
    "preview_pages", "_compose_answer_pages",
    "ANSWER_MODE_INLINE", "ANSWER_MODE_BLANK",
    "questions_brief", "collect_crops", "format_spec", "normalize_answers",
    "fit_answer_size", "estimate_line_count", "estimate_answer_height",
    "estimate_answer_lines",
    "humanize_answer", "semantic_breaks", "HARD_BREAK",
    "parse_question_number", "load_spec", "save_spec", "guess_kind",
]
