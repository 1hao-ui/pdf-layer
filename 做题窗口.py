# -*- coding: utf-8 -*-
"""做题窗口.py — 独立的「手写作业作答」窗口（绝不启动生成器 GUI）。

用途
----
    py -3 做题窗口.py
    py -3 做题窗口.py --open "C:\\Users\\a\\Downloads\\4第四章作业.pdf" ^
        --numbers "4.2.1,4.2.2,4.2.4,4.3.1,4.3.3,4.3.5,4.3.6"

做五件事：
    1. 打开作业页（PDF / 图片）；
    2. 自动划题：调用 worksheet.analyze()，只显示题目列表和答题区高亮，**不渲染、不写文件**；
    3. 手动查看/修改答案（编辑框实时进内存，切题不丢），可改字号/对齐/答题区 4 个 pt 数值；
    4. **实时叠加预览**：AI 作答 / 编辑答案后自动把手写效果叠到作业页上（纯内存，
       防抖 + 后台线程 + 按答案签名缓存），不导出 PDF 也能看到最终效果；预览支持
       滚轮缩放、拖拽平移、适合窗口；
    5. 只有用户主动点「生成手写作答」才调用 worksheet.render_answers() 写 PDF；
       输出目录与文件名可在界面自定义，默认 out/做题窗口_时间戳.pdf，
       也可点「另存为…」临时选位置。

约束
----
· 本模块 **不 import gui、不 subprocess、不唤起任何生成器窗口**；
· 导入本模块、构造窗口、打开作业页、实时预览都不会写任何输出文件
  （预览位图只存在内存里，来自 worksheet.preview_pages + page_model.render_page）；
· 实时预览与最终导出共用 worksheet 的排版内核（_compose_answer_pages），
  仅当用户改了某题字号/对齐时，通过一个 page_model 代理把该题的设置注入渲染器。
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import re
import sys
import threading
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import tkinter as tk                       # noqa: E402
from tkinter import filedialog, messagebox, ttk   # noqa: E402
from tkinter import font as tkfont         # noqa: E402

from PIL import Image, ImageTk             # noqa: E402

import worksheet                           # noqa: E402

try:                                       # 主题（可能不存在 / 无显示）；失败就走内置色板
    import theme as _theme
except Exception:                          # pragma: no cover
    _theme = None

try:                                       # PDF 页面预览
    import pymupdf as _fitz
except Exception:                          # pragma: no cover
    try:
        import fitz as _fitz
    except Exception:
        _fitz = None

# 冻结版（exe）把默认输出目录放到 exe 同目录，避免写进 PyInstaller 临时解压目录
APP_BASE = (os.path.dirname(os.path.abspath(sys.executable))
            if getattr(sys, "frozen", False) else HERE)
OUT_DIR = os.path.join(APP_BASE, "out")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")
DEFAULT_SIZE_PT = 12.4
LINE_RATIO = 1.85

ZOOM_MIN = 0.25
ZOOM_MAX = 4.0
LIVE_DEBOUNCE_MS = 450

ALIGN_LABELS = {"左对齐": "left", "居中": "center", "右对齐": "right"}


def _enable_dpi():
    """Windows DPI 感知（必须在创建 Tk 之前）；失败无害。"""
    try:
        if _theme is not None and hasattr(_theme, "set_dpi_aware"):
            _theme.set_dpi_aware()
            return
    except Exception:
        pass
    if os.name != "nt":
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi()


# ======================================================================
# 主题 / 内置兜底色板
# ======================================================================
FALLBACK_PALETTE = {
    "bg": "#F7F8FA", "card": "#FFFFFF", "card_alt": "#F2F4F8",
    "sidebar": "#F2F4F8", "toolbar": "#FFFFFF", "log": "#FFFFFF",
    "canvas": "#E9EBF2", "border": "#E6E8EF", "border_strong": "#D7DAE3",
    "text": "#1F2430", "muted": "#6B7280", "faint": "#9AA1AE",
    "accent": "#4F6BED", "accent_hover": "#3F59D6",
    "accent_soft": "#EEF1FE", "on_accent": "#FFFFFF",
    "danger": "#E5484D", "danger_soft": "#FDECEC",
    "selection": "#E3E8FC", "paper": "#FFFFFF", "paper_ink": "#1F2430",
    "success": "#22A06B",
}
FALLBACK_STYLES = {
    "frame": "TFrame", "card": "Card.TFrame", "toolbar": "Toolbar.TFrame",
    "log": "Log.TFrame", "card_box": "Card.TLabelframe",
    "card_title": "Card.TLabelframe.Label", "section": "SectionHeader.TLabel",
    "muted": "Muted.TLabel", "card_section": "Card.SectionHeader.TLabel",
    "card_muted": "Card.Muted.TLabel", "toolbar_label": "Toolbar.TLabel",
    "toolbar_muted": "Toolbar.Muted.TLabel", "button": "TButton",
    "accent_button": "Accent.TButton", "ghost_button": "Ghost.TButton",
    "entry": "TEntry", "combobox": "TCombobox", "spinbox": "TSpinbox",
    "separator": "TSeparator", "scrollbar": "TScrollbar",
}


def _fallback_tokens(root):
    """theme.py 不可用时的内置令牌 + 最小 ttk 样式配置。"""
    pal = dict(FALLBACK_PALETTE)
    styles = dict(FALLBACK_STYLES)
    fonts = {"body": ("Microsoft YaHei UI", 10),
             "muted": ("Microsoft YaHei UI", 9),
             "section": ("Microsoft YaHei UI", 10, "bold")}
    tk_tokens = {
        "canvas_bg": pal["canvas"], "text_bg": pal["card"],
        "log_text_bg": pal["card"], "input_bg": pal["card"],
        "select_bg": pal["accent"], "select_fg": pal["on_accent"],
        "paper": pal["paper"], "paper_ink": pal["paper_ink"],
    }
    try:
        st = ttk.Style(root)
        if "clam" in st.theme_names():
            st.theme_use("clam")
        st.configure("TFrame", background=pal["bg"])
        st.configure("Card.TFrame", background=pal["card"])
        st.configure("Toolbar.TFrame", background=pal["toolbar"])
        st.configure("TLabel", background=pal["bg"], foreground=pal["text"])
        st.configure("Card.TLabelframe", background=pal["card"],
                     bordercolor=pal["border"], borderwidth=1, relief="solid",
                     padding=(12, 10))
        st.configure("Card.TLabelframe.Label", background=pal["card"],
                     foreground=pal["text"],
                     font=("Microsoft YaHei UI", 10, "bold"))
        st.configure("TButton", background=pal["card_alt"],
                     foreground=pal["text"], bordercolor=pal["border"],
                     padding=(10, 5), relief="solid", borderwidth=1,
                     width=0)
        st.configure("Accent.TButton", background=pal["accent"],
                     foreground=pal["on_accent"], bordercolor=pal["accent"],
                     padding=(12, 6), relief="flat", borderwidth=1, width=0)
        st.map("Accent.TButton",
               background=[("active", pal["accent_hover"]),
                           ("pressed", pal["accent_hover"])])
        st.configure("Ghost.TButton", background=pal["card"],
                     foreground=pal["muted"], bordercolor=pal["card"],
                     padding=(8, 4), relief="flat", borderwidth=0, width=0)
        st.configure("TEntry", fieldbackground=pal["card"],
                     foreground=pal["text"], bordercolor=pal["border"],
                     padding=3)
        st.configure("TCombobox", fieldbackground=pal["card"],
                     foreground=pal["text"], padding=3)
        st.configure("TSpinbox", fieldbackground=pal["card"],
                     foreground=pal["text"], padding=3)
        st.configure("TSeparator", background=pal["border"])
        st.configure("TScrollbar", background=pal["border"],
                     troughcolor=pal["card"], arrowcolor=pal["text"],
                     relief="flat")
    except Exception:
        pass
    return {"PALETTE": pal, "STYLES": styles, "FONTS": fonts,
            "FONT_FAMILY": "Microsoft YaHei UI",
            "FONT_MONO": "Consolas", "TK": tk_tokens,
            "SIZES": {"section": 10.5, "muted": 9.0}, "SPACING": {},
            "DARK": False}


def _load_tokens(root):
    """尽量用 theme.py；失败返回内置兜底。"""
    if _theme is not None:
        try:
            tokens = _theme.apply_theme(root, dark=False)
            if isinstance(tokens, dict) and isinstance(tokens.get("PALETTE"), dict):
                return tokens
        except Exception:
            pass
    return _fallback_tokens(root)


def _tk_pal(tokens, key, default=""):
    try:
        return tokens["PALETTE"].get(key, default)
    except Exception:
        return FALLBACK_PALETTE.get(key, default)


# ======================================================================
# 两行卡片式题目列表
# ======================================================================
class _QuestionList(ttk.Frame):
    """Canvas 自绘的题目列表：第一行题号+摘要，第二行答题区/置信度。

    （不依赖生成器窗口模块；tk.Listbox 画不了双行和左侧主色条。）
    """

    def __init__(self, master, on_select=None, rows=6, dpi_scale=1.0):
        super().__init__(master)
        self.on_select = on_select
        self._items = []
        self._selected = None
        self._pal = {}
        self._rows = max(1, int(rows))
        self._dpi = max(1.0, float(dpi_scale or 1.0))
        self._title_font = None
        self._sub_font = None
        self._row_h = max(int(52 * self._dpi), 40)
        self._canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                 height=self._row_h * self._rows + 2)
        self._vsb = ttk.Scrollbar(self, orient="vertical",
                                  command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y")
        self._canvas.bind("<Configure>", lambda e: self.redraw())
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

    def set_palette(self, pal, family):
        self._pal = dict(pal or {})
        try:
            self._title_font = tkfont.Font(family=family, size=10)
            self._sub_font = tkfont.Font(family=family, size=9)
            self._row_h = max(
                int(52 * self._dpi),
                self._title_font.metrics("linespace")
                + self._sub_font.metrics("linespace")
                + int(12 * self._dpi))
        except Exception:
            self._title_font = self._sub_font = None
            self._row_h = max(int(52 * self._dpi), 40)
        try:
            self._canvas.configure(bg=self._pal.get("card", "#FFFFFF"),
                                   height=self._row_h * self._rows + 2)
        except Exception:
            pass
        self.redraw()

    def set_items(self, items, selected=None):
        self._items = list(items or [])
        if selected is not None and 0 <= int(selected) < len(self._items):
            self._selected = int(selected)
        elif self._selected is not None and self._selected >= len(self._items):
            self._selected = None
        self.redraw()

    def select(self, idx, notify=False):
        if idx is not None and not (0 <= int(idx) < len(self._items)):
            idx = None
        changed = idx != self._selected
        self._selected = None if idx is None else int(idx)
        self.redraw()
        if notify and changed and self.on_select:
            self.on_select(self._selected)

    def redraw(self):
        try:
            c = self._canvas
            c.delete("all")
            w = max(120, c.winfo_width())
            pal = self._pal or {}
            card = pal.get("card", "#FFFFFF")
            alt = pal.get("card_alt", "#F2F4F8")
            border = pal.get("border", "#E6E8EF")
            text = pal.get("text", "#1F2430")
            muted = pal.get("muted", "#6B7280")
            accent = pal.get("accent", "#4F6BED")
            soft = pal.get("accent_soft") or "#EEF1FE"
            if not self._items:
                c.create_text(w / 2.0, 40, text="打开作业页后自动划题",
                              fill=muted, font=self._sub_font)
                c.configure(scrollregion=(0, 0, w, 80))
                return
            for i, (title, sub) in enumerate(self._items):
                y = i * self._row_h
                sel = (i == self._selected)
                fill = soft if sel else alt
                c.create_rectangle(4, y + 3, w - 10, y + self._row_h - 3,
                                   fill=fill,
                                   outline=(accent if sel else border),
                                   width=1)
                if sel:
                    c.create_rectangle(4, y + 3, 7, y + self._row_h - 3,
                                       fill=accent, outline="")
                tx = 14
                ty = y + max(4, int(5 * self._dpi))
                maxw = max(60, w - 28)
                c.create_text(tx, ty, anchor="nw",
                              text=self._trim(title, self._title_font, maxw),
                              font=self._title_font, fill=text)
                line_h = (self._title_font.metrics("linespace")
                          if self._title_font else 18)
                c.create_text(tx, ty + line_h + 1, anchor="nw",
                              text=self._trim(sub, self._sub_font, maxw),
                              font=self._sub_font, fill=muted)
            total = max(1, self._row_h * len(self._items))
            c.configure(scrollregion=(0, 0, w, total))
            if self._selected is not None:
                c.yview_moveto(max(0.0, min(1.0,
                    (self._selected * self._row_h) / float(total))))
        except Exception:
            pass

    @staticmethod
    def _trim(text, font, maxw):
        text = text or ""
        if not font:
            return text
        try:
            if font.measure(text) <= maxw:
                return text
            while text and font.measure(text + "…") > maxw:
                text = text[:-1]
            return text + "…"
        except Exception:
            return text

    def _on_click(self, event):
        try:
            y = self._canvas.canvasy(event.y)
        except Exception:
            y = event.y
        idx = int(y // max(1, self._row_h))
        if 0 <= idx < len(self._items):
            self.select(idx, notify=True)

    def _on_wheel(self, event):
        try:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass
        return "break"


# ======================================================================
# page_model 代理：只把「用户改过的字号/对齐」注入 worksheet.render_answers
# ======================================================================
def make_page_model_shim(overrides):
    """构造一个可传给 worksheet.render_answers 的 page_model 代理。

    overrides: 与「有答案的题目（按 spec 顺序）」一一对应的 [(size_pt, align), ...]；
    size_pt 为 None 表示保留 worksheet 自动字号。排版/导出仍调用真正的 page_model。
    """
    import types
    import page_model as PM

    queue = list(overrides or [])

    class _Page(PM.Page):
        def add_paragraph(self, paragraph=None, **kw):
            p = super().add_paragraph(paragraph, **kw)
            if not queue:
                return p
            try:
                size, align = queue.pop(0)
            except Exception:
                return p
            if size:
                p.size_pt = float(size)
                p.line_pt = round(float(size) * LINE_RATIO, 2)
            if align in ("left", "center", "right"):
                p.align = align
            return p

    mod = types.ModuleType("worksheet_page_model_override")
    mod.Page = _Page
    mod.Paragraph = PM.Paragraph
    mod.export_pdf = PM.export_pdf
    mod.render_page = PM.render_page
    mod.SCALE = getattr(PM, "SCALE", 4)
    return mod


# ======================================================================
# 主窗口
# ======================================================================
def _icon_dirs():
    """图标可能的位置：打包解压目录 / 源码目录 / exe 目录。"""
    bases = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        bases.append(mei)
    bases.append(HERE)
    bases.append(APP_BASE)
    return [os.path.join(b, "assets", "icon") for b in bases if b]


def _set_window_icon(root):
    """窗口/任务栏图标：优先 PNG（Tk 8.6），失败再试 .ico。"""
    for d in _icon_dirs():
        for name in ("app_icon_256.png", "app_icon_64.png"):
            p = os.path.join(d, name)
            if os.path.exists(p):
                try:
                    img = tk.PhotoImage(file=p)
                    root.iconphoto(True, img)
                    root._zhi_icon = img      # 防止被 GC
                    return True
                except Exception:
                    pass
    for d in _icon_dirs():
        p = os.path.join(d, "app.ico")
        if os.path.exists(p):
            try:
                root.iconbitmap(default=p)
                return True
            except Exception:
                pass
    return False


class WorksheetWindow:
    """做题窗口（独立 Tk 窗口；绝不 import/subprocess 生成器窗口）。"""

    def __init__(self, root, dark=False):
        self.root = root
        self._dpi = self._detect_dpi()
        self.tokens = _load_tokens(root)
        self.pal = dict(self.tokens.get("PALETTE") or FALLBACK_PALETTE)
        self.styles = dict(self.tokens.get("STYLES") or FALLBACK_STYLES)
        self.tk_tokens = dict(self.tokens.get("TK") or {})
        self.font_family = (self.tokens.get("FONT_FAMILY")
                            or "Microsoft YaHei UI")
        self.font_mono = self.tokens.get("FONT_MONO") or "Consolas"

        # ---- 数据（打开文件/analyze 之后才有）----
        self.source = ""
        self.spec = None
        self.questions = []          # worksheet.questions_brief 的精简列表
        self._full_by_id = {}        # id -> spec 里的题目 dict（改答题区用）
        self.answers = {}            # id -> 答案文本（实时内存）
        self.sizes = {}              # id -> 字号 pt（用户改过才有）
        self.aligns = {}             # id -> left/center/right
        self.current = None          # 当前题目索引（self.questions 下标）
        self.page = 0
        self.generated_path = None
        self.preview_png_path = None
        self.last_coverage = None
        self.generated_mode = False
        self._generated_img = None
        self._page_cache = {}
        self._preview_img = None
        self._preview_photo = None
        self._syncing_editor = False
        self._log_open = False
        self._busy = False

        # ---- 实时叠加预览（纯内存，不落盘）----
        self.live_preview_on = True
        self._live_cache = {}        # 页下标 -> PIL.Image（作业底图 + 手写作答）
        self._live_sig = None        # 当前缓存对应的答案签名
        self._live_busy = False
        self._live_pending = False
        self._live_after = None
        self._live_queue = queue.Queue()
        # ---- 预览缩放 / 平移 ----
        self.zoom = 1.0              # 相对「适合窗口」的倍率
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._pan_last = None
        # ---- 输出路径（会话内记忆；窗口启动不写任何配置文件）----
        self._outname_custom = False

        root.title("ZTi 智能做题助手 · AI 作答 + 手写写回")
        _set_window_icon(root)
        self._setup_window_geometry()
        self._build_ui()
        self._apply_widget_colors()
        self._update_page_controls()
        self._update_status()
        self.log("做题窗口就绪：打开作业页后自动划题；只有点「生成手写作答」才写 PDF。")

    # ------------------------------------------------------------ 基础
    def _detect_dpi(self):
        try:
            return max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            return 1.0

    def _setup_window_geometry(self):
        try:
            sw = int(self.root.winfo_screenwidth())
            sh = int(self.root.winfo_screenheight())
        except Exception:
            sw, sh = 1366, 768
        w = min(max(1180, int(sw * 0.86)), max(900, sw - 40))
        h = min(max(760, int(sh * 0.86)), max(560, sh - 100))
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 3)
        try:
            self.root.minsize(min(1180, sw - 40), min(760, sh - 140))
            self.root.geometry("%dx%d+%d+%d" % (w, h, x, y))
        except Exception:
            pass

    def _style(self, key, default):
        return self.styles.get(key) or default

    # ------------------------------------------------------------ UI
    def _build_ui(self):
        outer = ttk.Frame(self.root, style=self._style("frame", "TFrame"))
        outer.pack(fill="both", expand=True)

        self._build_toolbar(outer)
        self._build_statusbar(outer)
        self._build_log(outer)

        body = ttk.PanedWindow(outer, orient="horizontal")
        body.pack(fill="both", expand=True, padx=6, pady=(2, 0))
        left = ttk.Frame(body, style=self._style("frame", "TFrame"), width=350)
        body.add(left, weight=0)
        right = ttk.Frame(body, style=self._style("frame", "TFrame"))
        body.add(right, weight=1)
        self._build_left(left)
        self._build_right(right)
        self.body_paned = body
        self._sash_done = False
        self._sash_tries = 0
        self._sash_after = None
        self._right_sash_done = False
        self._right_sash_tries = 0
        self._right_sash_after = None
        try:
            body.bind("<Configure>", self._on_body_configure, add="+")
            self._sash_after = self.root.after(60, self._set_sash_retry)
            self._right_sash_after = self.root.after(120, self._set_right_sash)
        except Exception:
            pass

    def _set_right_sash(self):
        """右侧「预览 / 答案编辑」初始各占约一半高度。"""
        self._right_sash_after = None
        if self._right_sash_done:
            return
        try:
            h = self.right_paned.winfo_height()
            if h > 400:
                self.right_paned.sashpos(0, int(h * 0.52))
                if 100 < self.right_paned.sashpos(0) < h - 100:
                    self._right_sash_done = True
                    return
        except Exception:
            pass
        self._right_sash_tries += 1
        if self._right_sash_tries < 30:
            try:
                self._right_sash_after = self.root.after(60, self._set_right_sash)
            except Exception:
                pass

    def _on_body_configure(self, event):
        if self._sash_done or event.width < 600:
            return
        try:
            self.body_paned.sashpos(0, 356)
            if self.body_paned.sashpos(0) >= 220:
                self._sash_done = True
        except Exception:
            pass

    def _set_sash_retry(self):
        """隐藏/未布局时 sashpos 可能无效；这里重试到窗口真正布局。"""
        self._sash_after = None
        if self._sash_done:
            return
        try:
            self.body_paned.sashpos(0, 356)
            if self.body_paned.sashpos(0) >= 220:
                self._sash_done = True
                return
        except Exception:
            pass
        self._sash_tries += 1
        if self._sash_tries < 30:
            try:
                self._sash_after = self.root.after(60, self._set_sash_retry)
            except Exception:
                pass

    def _build_toolbar(self, parent):
        bar = ttk.Frame(parent, style=self._style("toolbar", "Toolbar.TFrame"),
                        padding=(6, 4))
        bar.pack(side="top", fill="x")
        self.toolbar = bar
        pad = (10, 5)

        def btn(row, text, cmd, style=None, width=0):
            b = ttk.Button(row, text=text, command=cmd,
                           style=style or self._style("button", "TButton"),
                           padding=pad, width=width)
            b.pack(side="left", padx=1)
            return b

        def sep(row):
            ttk.Separator(row, orient="vertical").pack(side="left", fill="y",
                                                        padx=6, pady=3)

        row1 = ttk.Frame(bar, style=self._style("toolbar", "Toolbar.TFrame"))
        row1.pack(fill="x")
        btn(row1, "打开作业页", self._dlg_open_source)
        sep(row1)
        ttk.Button(row1, text="◀", command=lambda: self.go_page(-1),
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(6, 4), width=2).pack(side="left", padx=(0, 2))
        self.var_page = tk.StringVar(value="1")
        self.page_entry = ttk.Entry(row1, textvariable=self.var_page, width=4,
                                    justify="center")
        self.page_entry.pack(side="left")
        self.page_entry.bind("<Return>", lambda e: self._on_page_spin())
        self.page_entry.bind("<FocusOut>", lambda e: self._on_page_spin())
        ttk.Button(row1, text="▶", command=lambda: self.go_page(1),
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(6, 4), width=2).pack(side="left", padx=(2, 4))
        self.page_total_label = ttk.Label(
            row1, text="— / — 页",
            style=self._style("toolbar_muted", "Toolbar.Muted.TLabel"))
        self.page_total_label.pack(side="left", padx=(0, 4))
        sep(row1)
        btn(row1, "自动划区", self.reanalyze)
        ttk.Label(row1, text="题号(扫描件):",
                  style=self._style("toolbar_muted", "Toolbar.Muted.TLabel")
                  ).pack(side="left", padx=(6, 2))
        self.var_numbers = tk.StringVar(value="")
        self.numbers_entry = ttk.Entry(row1, textvariable=self.var_numbers,
                                       width=30)
        self.numbers_entry.pack(side="left", fill="x", expand=True, padx=(0, 4))
        self.numbers_entry.bind("<Return>", lambda e: self.reanalyze())
        ttk.Label(row1, text="逗号分隔",
                  style=self._style("toolbar_muted", "Toolbar.Muted.TLabel")
                  ).pack(side="left", padx=(0, 4))

        row2 = ttk.Frame(bar, style=self._style("toolbar", "Toolbar.TFrame"))
        row2.pack(fill="x", pady=(3, 0))
        btn(row2, "打开答案JSON", self._dlg_open_answers)
        btn(row2, "保存答案JSON", self._dlg_save_answers)
        sep(row2)
        self.btn_ai = ttk.Button(
            row2, text="AI 自动作答", command=self.ai_solve,
            style=self._style("accent_button", "Accent.TButton"),
            padding=(14, 6))
        self.btn_ai.pack(side="left", padx=1)
        btn(row2, "AI 设置", self._dlg_ai_settings)
        sep(row2)
        self.btn_generate = ttk.Button(
            row2, text="生成手写作答", command=self._generate_default,
            style=self._style("accent_button", "Accent.TButton"),
            padding=(14, 6), width=0)
        self.btn_generate.pack(side="left", padx=1)
        btn(row2, "另存为…", self._dlg_generate)
        btn(row2, "预览当前页", self.preview_current_page)
        ttk.Label(row2, text="AI 出答案 → 实时叠加预览 → 生成保存",
                  style=self._style("toolbar_muted", "Toolbar.Muted.TLabel")
                  ).pack(side="right", padx=6)

    def _build_statusbar(self, parent):
        bar = ttk.Frame(parent, style=self._style("toolbar", "Toolbar.TFrame"),
                        padding=(10, 3))
        bar.pack(side="bottom", fill="x")
        self.status_label = ttk.Label(
            bar, text="未打开作业页", anchor="w",
            style=self._style("toolbar_muted", "Toolbar.Muted.TLabel"))
        self.status_label.pack(side="left", fill="x", expand=True)
        self.hint_label = ttk.Label(
            bar, text="答题只改内存 · 实时预览不落盘 · 生成才写文件",
            style=self._style("toolbar_muted", "Toolbar.Muted.TLabel"))
        self.hint_label.pack(side="right")

    def _build_log(self, parent):
        wrap = ttk.Frame(parent, style=self._style("frame", "TFrame"))
        wrap.pack(side="bottom", fill="x", padx=8, pady=(3, 2))
        self._log_wrap = wrap
        head = ttk.Frame(wrap, style=self._style("toolbar", "Toolbar.TFrame"),
                         padding=(6, 0))
        head.pack(fill="x")
        self.log_btn = ttk.Button(
            head, text="▸ 日志", command=self.toggle_log,
            style=self._style("ghost_button", "Ghost.TButton"),
            padding=(6, 2))
        self.log_btn.pack(side="left")
        ttk.Label(head, text="分析/生成过程与 warning 都在这里",
                  style=self._style("toolbar_muted", "Toolbar.Muted.TLabel")
                  ).pack(side="left", padx=8)
        self.log_body = ttk.Frame(wrap, style=self._style("log", "TFrame"),
                                 padding=(8, 4))
        self.log_body.configure(height=120)
        self.log_body.pack_propagate(False)
        self.log_text = tk.Text(self.log_body, height=4, wrap="word",
                                state="disabled", relief="flat", bd=0)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(self.log_body, orient="vertical",
                           command=self.log_text.yview)
        sb.pack(side="left", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

    def toggle_log(self):
        if self._log_open:
            self.log_body.pack_forget()
            self.log_btn.configure(text="▸ 日志")
        else:
            self.log_body.pack(fill="x", pady=(2, 0))
            self.log_btn.configure(text="▾ 日志")
        self._log_open = not self._log_open

    def log(self, msg):
        try:
            stamp = datetime.now().strftime("%H:%M:%S")
            self.log_text.configure(state="normal")
            self.log_text.insert("end", "[%s] %s\n" % (stamp, str(msg)))
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _build_left(self, parent):
        card = ttk.Labelframe(parent, text="题目列表",
                              style=self._style("card_box", "Card.TLabelframe"),
                              padding=(10, 8))
        card.pack(fill="both", expand=True, padx=(0, 4), pady=2)
        self.count_label = ttk.Label(
            card, text="0 题",
            style=self._style("card_muted", "Card.Muted.TLabel"))
        self.count_label.pack(anchor="e")
        self.qlist = _QuestionList(card, on_select=self._on_list_select,
                                   rows=6, dpi_scale=self._dpi)
        self.qlist.pack(fill="both", expand=True, pady=(4, 0))
        self.left_card = card

    def _build_right(self, parent):
        paned = ttk.PanedWindow(parent, orient="vertical")
        paned.pack(fill="both", expand=True)
        self.right_paned = paned

        preview_card = ttk.Labelframe(
            paned, text="预览 · 第 — 页",
            style=self._style("card_box", "Card.TLabelframe"),
            padding=(8, 6))
        paned.add(preview_card, weight=3)
        self.preview_card = preview_card
        self._build_preview_toolbar(preview_card)
        self.canvas = tk.Canvas(preview_card, height=240,
                                bg=self.tk_tokens.get("canvas_bg")
                                or self.pal.get("canvas", "#E9EBF2"),
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_canvas())
        # 缩放 / 平移：滚轮以鼠标为锚点，按住左键拖拽，双击回到适合窗口
        self.canvas.bind("<MouseWheel>", self._on_canvas_wheel)
        self.canvas.bind("<Button-4>",
                         lambda e: self._zoom_at(e.x, e.y, 1.15))
        self.canvas.bind("<Button-5>",
                         lambda e: self._zoom_at(e.x, e.y, 1.0 / 1.15))
        self.canvas.bind("<ButtonPress-1>", self._on_pan_start)
        self.canvas.bind("<B1-Motion>", self._on_pan_move)
        self.canvas.bind("<ButtonRelease-1>",
                         lambda e: setattr(self, "_pan_last", None))
        self.canvas.bind("<Double-Button-1>", lambda e: self._zoom_fit())

        editor_card = ttk.Labelframe(
            paned, text="答案编辑",
            style=self._style("card_box", "Card.TLabelframe"),
            padding=(10, 8))
        paned.add(editor_card, weight=2)
        self.editor_card = editor_card
        self._build_editor(editor_card)

    def _build_preview_toolbar(self, parent):
        """预览卡片顶部：实时叠加开关 + 缩放控制。"""
        bar = ttk.Frame(parent, style=self._style("card", "Card.TFrame"))
        bar.pack(side="top", fill="x", pady=(0, 4))
        self.var_live = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="实时叠加答案", variable=self.var_live,
                        command=self._on_live_toggle).pack(side="left")
        ttk.Button(bar, text="刷新预览", command=self._refresh_live_preview,
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(8, 2)).pack(side="left", padx=(6, 10))
        ttk.Separator(bar, orient="vertical").pack(side="left", fill="y",
                                                    padx=4, pady=2)
        ttk.Button(bar, text="－", width=3,
                   command=lambda: self._zoom_step(1.0 / 1.25),
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(4, 2)).pack(side="left")
        self.var_zoom = tk.StringVar(value="100%")
        ttk.Label(bar, textvariable=self.var_zoom, width=7, anchor="center",
                  foreground=self.pal.get("accent", "#4F6BED"),
                  style=self._style("card_label", "Card.TLabel")).pack(side="left")
        ttk.Button(bar, text="＋", width=3,
                   command=lambda: self._zoom_step(1.25),
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(4, 2)).pack(side="left")
        ttk.Button(bar, text="适合窗口", command=self._zoom_fit,
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(8, 2)).pack(side="left", padx=(4, 0))
        ttk.Label(bar, text="滚轮缩放 · 拖拽平移 · 双击复位",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="right", padx=4)
        self._sync_zoom_label()

    def _build_editor(self, parent):
        self.cur_label = ttk.Label(
            parent, text="当前题：—",
            style=self._style("card_muted", "Card.Muted.TLabel"))
        self.cur_label.pack(anchor="w")

        text_row = ttk.Frame(parent, style=self._style("card", "Card.TFrame"))
        text_row.pack(fill="both", expand=True, pady=(3, 5))
        self.text_editor = tk.Text(text_row, height=5, wrap="word",
                                   relief="flat", bd=0, highlightthickness=1,
                                   undo=True)
        self.text_editor.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(text_row, orient="vertical",
                           command=self.text_editor.yview)
        sb.pack(side="left", fill="y")
        self.text_editor.configure(yscrollcommand=sb.set)
        self.text_editor.bind("<<Modified>>", self._on_editor_modified)
        self.text_editor.bind("<FocusOut>", lambda e: self._commit_editor())

        ctrl = ttk.Frame(parent, style=self._style("card", "Card.TFrame"))
        ctrl.pack(fill="x")
        ttk.Label(ctrl, text="字号 pt",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="left")
        self.var_size = tk.StringVar(value="%.1f" % DEFAULT_SIZE_PT)
        self.size_spin = ttk.Spinbox(ctrl, textvariable=self.var_size,
                                     from_=8, to=30, increment=0.2, width=6,
                                     command=self._on_size_align_change)
        self.size_spin.pack(side="left", padx=(3, 10))
        self.size_spin.bind("<Return>", lambda e: self._on_size_align_change())
        self.size_spin.bind("<FocusOut>",
                            lambda e: self._on_size_align_change())
        ttk.Label(ctrl, text="对齐",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="left")
        self.var_align = tk.StringVar(value="左对齐")
        self.align_box = ttk.Combobox(ctrl, textvariable=self.var_align,
                                      values=["左对齐", "居中", "右对齐"],
                                      state="readonly", width=7)
        self.align_box.pack(side="left", padx=(3, 10))
        self.align_box.bind("<<ComboboxSelected>>",
                            lambda e: self._on_size_align_change())
        self.size_hint = ttk.Label(
            ctrl, text="默认 12.4；只有改过才会覆盖自动字号",
            style=self._style("card_muted", "Card.Muted.TLabel"))
        self.size_hint.pack(side="left", padx=4)

        rect_row = ttk.Frame(parent, style=self._style("card", "Card.TFrame"))
        rect_row.pack(fill="x", pady=(5, 0))
        ttk.Label(rect_row, text="答题区 左/上/右/下 (pt)",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="left")
        self.var_rect = [tk.StringVar(value="") for _ in range(4)]
        for i in range(4):
            e = ttk.Entry(rect_row, textvariable=self.var_rect[i], width=6)
            e.pack(side="left", padx=(3, 0))
            e.bind("<Return>", lambda ev: self._apply_rect())
        ttk.Button(rect_row, text="应用",
                   command=self._apply_rect,
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(8, 3)).pack(side="left", padx=(8, 0))
        self.rect_hint = ttk.Label(
            rect_row, text="只影响高亮与生成",
            style=self._style("card_muted", "Card.Muted.TLabel"))
        self.rect_hint.pack(side="left", padx=6)

        # ---- 输出文件：目录 + 文件名（默认时间戳，均可自定义）----
        out_row = ttk.Frame(parent, style=self._style("card", "Card.TFrame"))
        out_row.pack(fill="x", pady=(6, 0))
        ttk.Label(out_row, text="输出目录",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="left")
        self.var_outdir = tk.StringVar(value=OUT_DIR)
        ttk.Entry(out_row, textvariable=self.var_outdir
                  ).pack(side="left", fill="x", expand=True, padx=(3, 0))
        ttk.Button(out_row, text="浏览…", command=self._dlg_choose_outdir,
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(8, 2)).pack(side="left", padx=(4, 10))
        ttk.Label(out_row, text="文件名",
                  style=self._style("card_muted", "Card.Muted.TLabel")
                  ).pack(side="left")
        self.var_outname = tk.StringVar(value=self._stamped_name())
        name_entry = ttk.Entry(out_row, textvariable=self.var_outname, width=26)
        name_entry.pack(side="left", padx=(3, 0))
        name_entry.bind("<KeyRelease>",
                        lambda e: setattr(self, "_outname_custom", True))
        ttk.Button(out_row, text="用时间戳", command=self._reset_outname,
                   style=self._style("ghost_button", "Ghost.TButton"),
                   padding=(8, 2)).pack(side="left", padx=4)
        self.out_hint = ttk.Label(
            parent, text="默认按时间命名；点「生成手写作答」直接存到这里，"
                         "或点「另存为…」临时换位置",
            style=self._style("card_muted", "Card.Muted.TLabel"))
        self.out_hint.pack(anchor="w", pady=(2, 0))

    def _apply_widget_colors(self):
        pal = self.pal
        tk_tokens = self.tk_tokens
        try:
            self.root.configure(bg=pal.get("bg", "#F7F8FA"))
        except Exception:
            pass
        try:
            self.canvas.configure(bg=tk_tokens.get("canvas_bg")
                                  or pal.get("canvas", "#E9EBF2"))
        except Exception:
            pass
        try:
            self.text_editor.configure(
                bg=tk_tokens.get("text_bg") or pal.get("card", "#FFFFFF"),
                fg=pal.get("paper_ink") or pal.get("text", "#1F2430"),
                insertbackground=pal.get("text", "#1F2430"),
                selectbackground=tk_tokens.get("select_bg") or pal.get("selection"),
                selectforeground=tk_tokens.get("select_fg") or pal.get("on_accent"),
                relief="flat", bd=0, highlightthickness=1,
                highlightbackground=pal.get("border", "#E6E8EF"),
                highlightcolor=pal.get("accent", "#4F6BED"))
        except Exception:
            pass
        try:
            self.log_text.configure(
                bg=tk_tokens.get("log_text_bg") or pal.get("log", "#FFFFFF"),
                fg=pal.get("text", "#1F2430"),
                insertbackground=pal.get("text", "#1F2430"))
        except Exception:
            pass
        try:
            self.qlist.set_palette(pal, self.font_family)
        except Exception:
            pass

    # ------------------------------------------------------------ 状态
    def _selected_question(self):
        if self.current is None:
            return None
        if 0 <= self.current < len(self.questions):
            return self.questions[self.current]
        return None

    def _filled_count(self):
        return sum(1 for q in self.questions
                   if (self.answers.get(q.get("id")) or "").strip())

    def _update_status(self):
        parts = []
        if self.source:
            parts.append(os.path.basename(self.source))
        if self.spec:
            pages = self.spec.get("pages") or []
            parts.append("第 %d/%d 页" % (self.page + 1, max(1, len(pages))))
            parts.append("%d 题" % len(self.questions))
            parts.append("已填 %d 题" % self._filled_count())
        else:
            parts.append("未打开作业页")
        try:
            self.status_label.configure(text=" · ".join(parts))
        except Exception:
            pass
        try:
            self.count_label.configure(text="%d 题" % len(self.questions))
        except Exception:
            pass
        try:
            self.page_total_label.configure(
                text=("%d / %d 页" % (self.page + 1,
                                      max(1, len(self.spec.get("pages") or []))
                                      ) if self.spec else "— / — 页"))
        except Exception:
            pass
        try:
            self.var_page.set(str(self.page + 1) if self.spec else "1")
        except Exception:
            pass
        try:
            if self.spec:
                total = max(1, len(self.spec.get("pages") or []))
                if self.generated_mode:
                    title = "生成预览 · 第 %d 页" % (self.page + 1)
                elif (self.live_preview_on
                      and self.page in self._live_cache):
                    title = ("实时预览 · 第 %d/%d 页（答案已叠加，未保存）"
                             % (self.page + 1, total))
                else:
                    title = "预览 · 第 %d/%d 页" % (self.page + 1, total)
                self.preview_card.configure(text=title)
            else:
                self.preview_card.configure(text="预览")
        except Exception:
            pass

    def _update_page_controls(self):
        try:
            self.page_entry.configure(state="normal" if self.spec else "disabled")
            self.numbers_entry.configure(state="normal")
        except Exception:
            pass

    def _set_busy(self, busy, text=""):
        self._busy = bool(busy)
        for attr in ("btn_generate", "btn_ai"):
            try:
                getattr(self, attr).configure(
                    state=("disabled" if busy else "normal"))
            except Exception:
                pass
        if text:
            try:
                self.status_label.configure(text=text)
                self.root.update_idletasks()
            except Exception:
                pass

    # ------------------------------------------------------------ 打开 / 分析
    def _warn(self, msg, title="提示"):
        self.log(msg)
        try:
            if self.root.winfo_ismapped():
                messagebox.showwarning(title, msg, parent=self.root)
        except Exception:
            pass

    def _parse_numbers(self):
        raw = (self.var_numbers.get() or "")
        raw = raw.replace("，", ",")
        parts = [p.strip() for p in re.split(r"[,，;；\s]+", raw) if p.strip()]
        return parts or None

    def load_source(self, path, numbers=None, page="all"):
        """打开作业页并 analyze（只划区显示，不渲染、不写文件）。"""
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(str(path))))
        if not os.path.exists(path):
            self._warn("文件不存在：%s" % path)
            return False
        if numbers is None:
            numbers = self._parse_numbers()
        elif isinstance(numbers, str):
            numbers = [p.strip() for p in re.split(r"[,，;；\s]+", numbers)
                       if p.strip()] or None
        self._set_busy(True, "正在分析版面：%s …" % os.path.basename(path))
        try:
            spec = worksheet.analyze(path, page=page, numbers=numbers)
        except Exception:
            tb = traceback.format_exc()
            self._warn("分析失败：\n" + tb)
            self._set_busy(False)
            self._update_status()
            return False
        total = sum(len(pg.get("questions") or [])
                    for pg in (spec.get("pages") or []))
        if total == 0:
            self._warn("没有识别到题目：可以试试「自动划区」并填写题号，"
                       "或确认页面是作业/试卷。")
            self._set_busy(False)
            return False
        self.source = path
        self.spec = spec
        self.questions = worksheet.questions_brief(spec)
        self._full_by_id = {}
        for pg in spec.get("pages") or []:
            for q in pg.get("questions") or []:
                self._full_by_id[str(q.get("id"))] = q
        self.answers = {}
        self.sizes = {}
        self.aligns = {}
        self.generated_path = None
        self.preview_png_path = None
        self.generated_mode = False
        self._generated_img = None
        self._page_cache = {}
        self._invalidate_live()
        if not getattr(self, "_outname_custom", False):
            try:
                self.var_outname.set(self._stamped_name())
            except Exception:
                pass
        self.page = 0
        self.current = 0 if self.questions else None
        self._rebuild_question_views()
        self._sync_editor_from_current()
        self._update_status()
        self._update_page_controls()
        self._redraw_canvas()
        self.log("已分析：%s（%d 页 %d 题）；尚未渲染、未写任何文件。"
                 % (os.path.basename(path), len(spec.get("pages") or []), total))
        for w in (spec.get("warnings") or [])[:12]:
            self.log("[warning] " + str(w))
        self._set_busy(False)
        self._update_status()
        return True

    def reanalyze(self):
        """重新 analyze 当前文件（可改题号后重跑），尽量保留已填答案。"""
        if not self.source:
            self._warn("还没有打开作业页")
            return False
        old = []
        for q in self.questions:
            text = self.answers.get(q.get("id"))
            if text is not None:
                old.append((str(q.get("number") or ""), text))
        ok = self.load_source(self.source, numbers=self._parse_numbers(),
                              page="all")
        if not ok:
            return False
        # 按题号把旧答案映射回新题（题号一致才保留）
        if old:
            by_num = {}
            for num, text in old:
                by_num.setdefault(num, text)
            restored = 0
            for q in self.questions:
                num = str(q.get("number") or "")
                if num in by_num and (by_num[num] or "").strip():
                    self.answers[str(q.get("id"))] = by_num[num]
                    restored += 1
            self._sync_editor_from_current()
            self._rebuild_question_views()
            self._update_status()
            self._schedule_live_preview(True, 80)
            self.log("重新划区完成，按题号保留 %d 条已填答案。" % restored)
        return True

    def _dlg_open_source(self):
        try:
            path = filedialog.askopenfilename(
                parent=self.root, title="选择作业页（PDF / 图片）",
                filetypes=[("作业页", "*.pdf *.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                           ("PDF", "*.pdf"), ("图片", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
                           ("所有文件", "*.*")])
        except Exception:
            return
        if path:
            self.load_source(path)

    def _on_page_spin(self):
        if not self.spec:
            return
        try:
            idx = int(self.var_page.get()) - 1
        except Exception:
            return
        self._goto_page(idx)

    def go_page(self, delta):
        self._goto_page(self.page + int(delta))

    def _goto_page(self, idx):
        pages = (self.spec or {}).get("pages") or []
        if not pages:
            return
        idx = max(0, min(int(idx), len(pages) - 1))
        self.page = idx
        self.generated_mode = False
        self._generated_img = None
        self._redraw_canvas()
        self._update_status()
        self._schedule_live_preview()

    # ------------------------------------------------------------ 题目列表 / 编辑
    def _rebuild_question_views(self):
        items = []
        for q in self.questions:
            num = str(q.get("number") or q.get("id") or "?")
            text = (q.get("text") or "").strip()
            if text:
                summary = re.sub(r"\s+", " ", text)
                title = "%s  %s" % (num, summary)
            else:
                title = "%s  （扫描件，无文字层）" % num
            conf = q.get("confidence")
            conf_txt = ("置信度 %.0f%%" % (100.0 * float(conf))
                        if conf is not None else "置信度 —")
            sub = "第%d页 · 答题区%d行 · %s" % (
                int(q.get("page", 0)) + 1, int(q.get("answer_lines") or 0),
                conf_txt)
            items.append((title, sub))
        try:
            self.qlist.set_items(items, selected=self.current)
        except Exception:
            pass

    def _on_list_select(self, idx):
        if idx is None:
            return
        if idx == self.current:
            return
        self._commit_editor()
        self.select_question(idx)

    def select_question(self, idx):
        if not (0 <= int(idx) < len(self.questions)):
            return
        self._commit_editor()
        self.current = int(idx)
        q = self.questions[self.current]
        self.page = int(q.get("page") or 0)
        self.generated_mode = False
        self._generated_img = None
        try:
            self.qlist.select(self.current)
        except Exception:
            pass
        self._sync_editor_from_current()
        self._redraw_canvas()
        self._update_status()
        self._schedule_live_preview()

    def set_answer(self, text, commit=True):
        """程序化写入当前题答案（测试/外部调用）。"""
        q = self._selected_question()
        if q is None:
            return False
        qid = str(q.get("id"))
        if (text or "").strip():
            self.answers[qid] = text
        else:
            self.answers.pop(qid, None)
        if commit:
            self._syncing_editor = True
            try:
                self.text_editor.delete("1.0", "end")
                self.text_editor.insert("1.0", text or "")
                self.text_editor.edit_modified(False)
            finally:
                self._syncing_editor = False
        self._update_status()
        self._schedule_live_preview(True, 60)
        return True

    def _sync_editor_from_current(self):
        q = self._selected_question()
        self._syncing_editor = True
        try:
            if q is None:
                self.cur_label.configure(text="当前题：—")
                self.text_editor.delete("1.0", "end")
                for i in range(4):
                    self.var_rect[i].set("")
                return
            qid = str(q.get("id"))
            self.cur_label.configure(
                text="当前题：%s · 第 %d 页 · 答题区 %d 行" % (
                    q.get("number") or qid, int(q.get("page", 0)) + 1,
                    int(q.get("answer_lines") or 0)))
            self.text_editor.delete("1.0", "end")
            self.text_editor.insert("1.0", self.answers.get(qid) or "")
            self.text_editor.edit_modified(False)
            size = self.sizes.get(qid, DEFAULT_SIZE_PT)
            self.var_size.set("%.1f" % float(size))
            align = self.aligns.get(qid, "left")
            label = {"left": "左对齐", "center": "居中",
                     "right": "右对齐"}.get(align, "左对齐")
            self.var_align.set(label)
            rect = q.get("answer_rect") or [56, 120, 539, 300]
            for i in range(4):
                self.var_rect[i].set(("%.1f" % float(rect[i])).rstrip("0").rstrip("."))
        except Exception:
            pass
        finally:
            self._syncing_editor = False

    def _on_editor_modified(self, _event=None):
        try:
            modified = self.text_editor.edit_modified()
        except Exception:
            modified = False
        if not modified:
            return
        try:
            self.text_editor.edit_modified(False)
        except Exception:
            pass
        if self._syncing_editor:
            return
        self._commit_editor()
        self._update_status()
        self._schedule_live_preview()

    def _commit_editor(self):
        q = self._selected_question()
        if q is None:
            return
        try:
            text = self.text_editor.get("1.0", "end").rstrip("\n")
        except Exception:
            return
        qid = str(q.get("id"))
        if text.strip():
            self.answers[qid] = text
        else:
            self.answers.pop(qid, None)

    def _on_size_align_change(self):
        q = self._selected_question()
        if q is None:
            return
        self._commit_editor()
        qid = str(q.get("id"))
        try:
            size = max(8.0, min(30.0, float(self.var_size.get())))
            self.sizes[qid] = round(size, 2)
            self.var_size.set("%.1f" % size)
        except Exception:
            pass
        self.aligns[qid] = ALIGN_LABELS.get(self.var_align.get(), "left")
        self._update_status()
        self.log("已记下 %s 的生成设置：字号 %.1fpt · %s（生成时生效）"
                 % (q.get("number") or qid, self.sizes.get(qid, DEFAULT_SIZE_PT),
                    self.var_align.get()))
        self._schedule_live_preview()

    def _apply_rect(self):
        q = self._selected_question()
        if q is None:
            return False
        try:
            rect = [float(v.get()) for v in self.var_rect]
        except Exception:
            self._warn("答题区 4 个数值都要是数字（pt）")
            return False
        left, top, right, bottom = rect
        if not (right - left >= 20 and bottom - top >= 14):
            self._warn("答题区太小：宽至少 20pt、高至少 14pt")
            return False
        pages = (self.spec or {}).get("pages") or []
        pw = ph = 0.0
        if 0 <= int(q.get("page", 0)) < len(pages):
            pw = float(pages[int(q.get("page", 0))].get("width_pt") or 595.0)
            ph = float(pages[int(q.get("page", 0))].get("height_pt") or 842.0)
        if left < 0 or top < 0 or right > pw + 0.5 or bottom > ph + 0.5:
            self._warn("答题区超出页面范围（%.0f × %.0f pt）" % (pw, ph))
            return False
        rect = [round(left, 2), round(top, 2), round(right, 2), round(bottom, 2)]
        qid = str(q.get("id"))
        q["answer_rect"] = list(rect)
        full = self._full_by_id.get(qid)
        if full is not None:
            full["answer_rect"] = list(rect)
        self.log("已更新 %s 答题区：%s" % (q.get("number") or qid, rect))
        self._schedule_live_preview()
        self._redraw_canvas()
        self._update_status()
        return True

    # ------------------------------------------------------------ 页码 / 预览
    def _page_dims(self, page_idx):
        pages = (self.spec or {}).get("pages") or []
        if 0 <= int(page_idx) < len(pages):
            pg = pages[int(page_idx)]
            return (float(pg.get("width_pt") or 595.0),
                    float(pg.get("height_pt") or 842.0))
        return (595.0, 842.0)

    def _load_page_image(self, page_idx):
        """页面背景图（仅内存缓存，不写文件）。"""
        key = (self.source, int(page_idx))
        if key in self._page_cache:
            return self._page_cache[key]
        kind = (self.spec or {}).get("kind") or ""
        img = None
        if kind == "pdf" and _fitz is not None:
            try:
                doc = _fitz.open(self.source)
                if 0 <= int(page_idx) < len(doc):
                    page = doc[int(page_idx)]
                    zoom = 150.0 / 72.0
                    pix = page.get_pixmap(matrix=_fitz.Matrix(zoom, zoom),
                                          alpha=False)
                    img = Image.frombytes("RGB", (pix.width, pix.height),
                                          pix.samples)
                doc.close()
            except Exception:
                img = None
        if img is None:
            try:
                img = Image.open(self.source).convert("RGB")
            except Exception:
                img = None
        if img is not None:
            self._page_cache[key] = img
        return img

    def _canvas_size(self):
        try:
            return (max(1, self.canvas.winfo_width()),
                    max(1, self.canvas.winfo_height()))
        except Exception:
            return (1, 1)

    def _fit_image(self, img, cw, ch, margin=24):
        if img is None or cw < 10 or ch < 10:
            return 0.0, 0.0, 0.0
        z = min((cw - 2 * margin) / float(img.width),
                (ch - 2 * margin) / float(img.height))
        z = max(0.02, z)
        w, h = img.width * z, img.height * z
        return z, (cw - w) / 2.0, (ch - h) / 2.0

    def _page_to_screen(self, x_pt, y_pt, page_idx, img, z, ox, oy):
        pw, ph = self._page_dims(page_idx)
        return (ox + (float(x_pt) / max(1e-6, pw)) * img.width * z,
                oy + (float(y_pt) / max(1e-6, ph)) * img.height * z)

    # -------------------------------------------- 实时叠加预览（纯内存，不落盘）
    def _invalidate_live(self):
        self._live_cache = {}
        self._live_sig = None
        self._live_pending = False
        if self._live_after is not None:
            try:
                self.root.after_cancel(self._live_after)
            except Exception:
                pass
            self._live_after = None

    def _answer_signature(self, answers=None):
        """答案 / 字号 / 对齐 / 答题区 的综合签名，用于缓存失效判断。"""
        a = answers if answers is not None else {
            k: v for k, v in self.answers.items() if (v or "").strip()}
        rects = {str(q.get("id")): list(q.get("answer_rect") or [])
                 for q in self.questions}
        try:
            return json.dumps(
                [sorted((str(k), str(v)) for k, v in a.items()),
                 sorted((str(k), float(v)) for k, v in self.sizes.items()),
                 sorted((str(k), str(v)) for k, v in self.aligns.items()),
                 sorted((k, v) for k, v in rects.items())],
                ensure_ascii=False, sort_keys=True, default=str)
        except Exception:
            return repr(sorted(a.items()))

    def _schedule_live_preview(self, force=False, delay=LIVE_DEBOUNCE_MS):
        """答案变化后防抖触发一次内存渲染；签名未变且当前页已缓存则跳过。"""
        if not self.live_preview_on or not self.spec:
            return
        try:
            sig = self._answer_signature()
            if sig != self._live_sig:
                self._live_cache = {}
                self._live_sig = sig
            elif not force and self.page in self._live_cache:
                return
            if self._live_busy or self._busy:
                self._live_pending = True
                return
            if self._live_after is not None:
                try:
                    self.root.after_cancel(self._live_after)
                except Exception:
                    pass
            self._live_after = self.root.after(
                int(delay), lambda: self._start_live_render())
        except Exception:
            pass

    def _start_live_render(self):
        self._live_after = None
        if not self.live_preview_on or not self.spec:
            return
        if self._live_busy or self._busy:
            self._live_pending = True
            return
        answers = {k: v for k, v in self.answers.items()
                   if (v or "").strip()}
        if not answers:
            self._invalidate_live()
            self._redraw_canvas()
            self._update_status()
            return
        sig = self._answer_signature(answers)
        page_idx = int(self.page)
        spec = self.spec
        try:
            overrides = self._build_overrides(set(answers))
            shim = (make_page_model_shim(overrides)
                    if any(s is not None or a is not None
                           for s, a in overrides) else None)
        except Exception:
            shim = None
        self._live_busy = True
        self._live_sig = sig

        def worker():
            try:
                imgs, warns, cov = worksheet.preview_pages(
                    spec, answers, page_model=shim, only_page=page_idx)
                self._live_queue.put((sig, imgs, warns, cov, None))
            except Exception as e:            # noqa: BLE001
                self._live_queue.put((sig, {}, [], {}, e))

        threading.Thread(target=worker, daemon=True,
                         name="live-preview").start()
        try:
            self.root.after(60, self._live_poll)
        except Exception:
            pass

    def _live_poll(self):
        try:
            item = self._live_queue.get_nowait()
        except queue.Empty:
            try:
                self.root.after(60, self._live_poll)
            except Exception:
                pass
            return
        sig, imgs, warns, cov, err = item
        self._live_busy = False
        try:
            if err is not None:
                self.log("实时预览失败：%s" % err)
            elif sig == self._live_sig:
                self._live_cache.update(imgs)
                for w in (warns or [])[:3]:
                    self.log("[实时预览 warning] " + str(w))
            self._redraw_canvas()
            self._update_status()
        except Exception:
            pass
        # 期间又改了答案 / 换了页：再补一次
        try:
            if self._live_pending or (
                    self.live_preview_on and self.spec
                    and self.page not in self._live_cache
                    and self._answer_signature() == self._live_sig):
                self._live_pending = False
                self.root.after(
                    80, lambda: self._schedule_live_preview(True, 60))
        except Exception:
            pass

    def _refresh_live_preview(self):
        if not self.live_preview_on:
            try:
                self.var_live.set(True)
            except Exception:
                pass
            self._on_live_toggle()
        self._invalidate_live()
        self._schedule_live_preview(True, 0)

    def _on_live_toggle(self):
        self.live_preview_on = bool(self.var_live.get())
        if self.live_preview_on:
            self.generated_mode = False
            self._generated_img = None
            self._schedule_live_preview(True, 60)
        else:
            self._invalidate_live()
        self._redraw_canvas()
        self._update_status()

    # ------------------------------------------------------ 预览缩放 / 平移
    def _sync_zoom_label(self):
        try:
            self.var_zoom.set("%d%%" % round(self.zoom * 100))
        except Exception:
            pass

    def _on_canvas_wheel(self, event):
        delta = getattr(event, "delta", 0)
        if not delta:
            return
        self._zoom_at(event.x, event.y,
                      1.15 if delta > 0 else 1.0 / 1.15)

    def _zoom_at(self, mx, my, factor):
        old = self.zoom
        new = max(ZOOM_MIN, min(ZOOM_MAX, old * factor))
        if abs(new - old) < 1e-9:
            return
        img, _mode = self._display_image()
        if img is not None:
            cw, ch = self._canvas_size()
            z0, ox0, oy0 = self._view_geom(img, cw, ch)
            if z0 > 1e-6:
                px = (mx - ox0) / z0
                py = (my - oy0) / z0
                z1 = z0 * (new / old)
                self.pan_x = (mx - px * z1) - (cw - img.width * z1) / 2.0
                self.pan_y = (my - py * z1) - (ch - img.height * z1) / 2.0
        self.zoom = new
        self._sync_zoom_label()
        self._redraw_canvas()

    def _zoom_step(self, factor):
        cw, ch = self._canvas_size()
        self._zoom_at(cw / 2.0, ch / 2.0, factor)

    def _zoom_fit(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._sync_zoom_label()
        self._redraw_canvas()

    def _on_pan_start(self, event):
        self._pan_last = (event.x, event.y)

    def _on_pan_move(self, event):
        if not self._pan_last:
            return
        dx = event.x - self._pan_last[0]
        dy = event.y - self._pan_last[1]
        self._pan_last = (event.x, event.y)
        self.pan_x += dx
        self.pan_y += dy
        self._redraw_canvas()

    def _view_geom(self, img, cw, ch, margin=24):
        """按当前 zoom / pan 计算 (z, ox, oy)。"""
        z_fit, _, _ = self._fit_image(img, cw, ch, margin)
        z = z_fit * self.zoom
        ox = (cw - img.width * z) / 2.0 + self.pan_x
        oy = (ch - img.height * z) / 2.0 + self.pan_y
        return z, ox, oy

    def _display_image(self):
        """返回 (img, mode)：mode ∈ generated / live / layout / none。"""
        if self.generated_mode and self._generated_img is not None:
            return self._generated_img, "generated"
        if (self.live_preview_on and self.spec
                and self.page in self._live_cache):
            return self._live_cache[self.page], "live"
        if not self.spec:
            return None, "none"
        pages = self.spec.get("pages") or []
        page_idx = min(self.page, max(0, len(pages) - 1))
        return self._load_page_image(page_idx), "layout"

    # ------------------------------------------------------ 输出路径 / 文件名
    def _stamped_name(self):
        return "做题窗口_%s.pdf" % datetime.now().strftime("%Y%m%d_%H%M%S")

    def _compose_out_path(self):
        folder = (self.var_outdir.get() or "").strip() or OUT_DIR
        name = (self.var_outname.get() or "").strip() or self._stamped_name()
        if not name.lower().endswith(".pdf"):
            name += ".pdf"
        folder = os.path.abspath(os.path.expanduser(os.path.expandvars(folder)))
        return os.path.join(folder, os.path.basename(name))

    def _reset_outname(self):
        self.var_outname.set(self._stamped_name())
        self._outname_custom = False
        self.log("输出文件名已重置为当前时间戳：%s" % self.var_outname.get())

    def _dlg_choose_outdir(self):
        try:
            path = filedialog.askdirectory(
                parent=self.root, title="选择输出目录",
                initialdir=(self.var_outdir.get() or OUT_DIR))
        except Exception:
            return
        if path:
            self.var_outdir.set(path)
            self.log("输出目录：%s" % path)

    def _generate_default(self):
        if not self.spec or not self.questions:
            self._warn("先打开作业页并自动划题。")
            return
        self.generate(out_pdf=self._compose_out_path())

    def _redraw_canvas(self):
        if not hasattr(self, "canvas"):
            return
        c = self.canvas
        cw, ch = self._canvas_size()
        if cw < 10 or ch < 10:
            return
        pal = self.pal
        try:
            c.delete("all")
            self._sync_zoom_label()
            img, mode = self._display_image()
            if img is None:
                c.create_text(
                    cw / 2.0, ch / 2.0,
                    text=("当前页预览加载失败" if self.spec else
                          "打开作业页（PDF / 图片）后自动划题\n"
                          "不会自动生成、不会写文件"),
                    fill=pal.get("danger" if self.spec else "muted",
                                 "#6B7280"),
                    font=(self.font_family, 11), justify="center")
                return
            z, ox, oy = self._view_geom(img, cw, ch)
            # 保护：缩放过大时不生成巨型位图
            tw = max(1, min(8000, int(img.width * z)))
            th = max(1, min(8000, int(img.height * z)))
            shown = img.resize(
                (tw, th), getattr(Image, "Resampling", Image).LANCZOS)
            self._preview_photo = ImageTk.PhotoImage(shown)
            c.create_image(ox, oy, anchor="nw", image=self._preview_photo)
            c.create_rectangle(ox, oy, ox + tw, oy + th,
                               outline=pal.get("border", "#E6E8EF"))
            if mode == "generated":
                c.create_text(
                    10, 8, anchor="nw",
                    text="生成预览 · %s（点「预览当前页」返回实时叠加）"
                         % os.path.basename(self.generated_path or ""),
                    fill=pal.get("muted", "#6B7280"),
                    font=(self.font_family, 9))
                return
            if mode == "live":
                c.create_text(
                    10, 8, anchor="nw",
                    text="实时叠加 · 已写入 %d 题答案（未保存，仅内存预览）"
                         % self._filled_count(),
                    fill=pal.get("accent", "#4F6BED"),
                    font=(self.font_family, 9))
                # 实时预览下只标当前题答题区（细虚线），不遮挡手写字迹
                q = self._selected_question()
                if q is not None and int(q.get("page") or 0) == self.page:
                    rect = q.get("answer_rect") or []
                    if len(rect) == 4:
                        x0, y0 = self._page_to_screen(
                            rect[0], rect[1], self.page, img, z, ox, oy)
                        x1, y1 = self._page_to_screen(
                            rect[2], rect[3], self.page, img, z, ox, oy)
                        c.create_rectangle(
                            x0, y0, x1, y1,
                            outline=pal.get("accent", "#4F6BED"),
                            dash=(3, 4), width=1)
                return
            # ---- 版面视图：原作业页 + 答题区高亮 ----
            pages = self.spec.get("pages") or []
            page_idx = min(self.page, max(0, len(pages) - 1))
            pw, ph = self._page_dims(page_idx)
            for q in self.questions:
                if int(q.get("page") or 0) != page_idx:
                    continue
                rect = q.get("answer_rect") or []
                if len(rect) != 4:
                    continue
                x0, y0 = self._page_to_screen(
                    rect[0], rect[1], page_idx, img, z, ox, oy)
                x1, y1 = self._page_to_screen(
                    rect[2], rect[3], page_idx, img, z, ox, oy)
                cur = (self._selected_question() is not None
                       and self._selected_question().get("id") == q.get("id"))
                if cur:
                    continue
                c.create_rectangle(
                    x0, y0, x1, y1,
                    outline=pal.get("border_strong",
                                    pal.get("border", "#D7DAE3")),
                    dash=(4, 3), width=1)
            q = self._selected_question()
            if q is not None and int(q.get("page") or 0) == page_idx:
                qrect = q.get("rect") or []
                if len(qrect) == 4:
                    x0, y0 = self._page_to_screen(
                        qrect[0], qrect[1], page_idx, img, z, ox, oy)
                    x1, y1 = self._page_to_screen(
                        qrect[2], qrect[3], page_idx, img, z, ox, oy)
                    c.create_rectangle(
                        x0, y0, x1, y1,
                        outline=pal.get("muted", "#6B7280"),
                        dash=(2, 2), width=1)
                rect = q.get("answer_rect") or []
                if len(rect) == 4:
                    x0, y0 = self._page_to_screen(
                        rect[0], rect[1], page_idx, img, z, ox, oy)
                    x1, y1 = self._page_to_screen(
                        rect[2], rect[3], page_idx, img, z, ox, oy)
                    c.create_rectangle(
                        x0, y0, x1, y1,
                        fill=pal.get("accent_soft", "#EEF1FE"),
                        stipple="gray25",
                        outline=pal.get("accent", "#4F6BED"), width=2)
                    c.create_text(
                        x0 + 4, y0 + 2, anchor="nw",
                        text="答题区 · 约 %d 行"
                             % int(q.get("answer_lines") or 0),
                        fill=pal.get("accent", "#4F6BED"),
                        font=(self.font_family, 9))
            c.create_text(
                10, 8, anchor="nw",
                text="第 %d/%d 页 · %.0f×%.0f pt · %s"
                     % (page_idx + 1, max(1, len(pages)), pw, ph,
                        os.path.basename(self.source)),
                fill=pal.get("muted", "#6B7280"),
                font=(self.font_family, 9))
        except Exception:
            self.log("预览绘制失败：\n" + traceback.format_exc())

    def preview_current_page(self):
        self.generated_mode = False
        self._generated_img = None
        self._redraw_canvas()
        self._update_status()
        self._schedule_live_preview(True, 60)

    # ------------------------------------------------------------ 答案 JSON
    def _questions_full(self):
        out = []
        for pg in (self.spec or {}).get("pages") or []:
            for q in pg.get("questions") or []:
                out.append(q)
        return out

    def _display_key(self, q):
        """存 JSON 用：题号（同题号跨页时退回唯一 id）。"""
        num = str(q.get("number") or "").strip()
        if num:
            same = [x for x in self.questions
                    if str(x.get("number") or "").strip() == num]
            if len(same) <= 1:
                return num
        return str(q.get("id"))

    def save_answers(self, path=None):
        self._commit_editor()
        payload = {}
        for q in self.questions:
            qid = str(q.get("id"))
            text = self.answers.get(qid)
            if text is not None and text.strip():
                payload[self._display_key(q)] = text
        if not payload:
            self._warn("还没有填写任何答案。")
            return None
        if not path:
            path = self._default_answers_path()
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
        try:
            folder = os.path.dirname(path)
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump({"answers": payload}, fp, ensure_ascii=False,
                          indent=2)
            self.log("答案已保存：%s（%d 题）" % (path, len(payload)))
            return path
        except Exception:
            self._warn("保存答案失败：\n" + traceback.format_exc())
            return None

    def load_answers(self, path):
        """读 {"answers": {...}}（也兼容纯 {题号: 文本} / 列表写法）。"""
        if not self.spec or not self.questions:
            self._warn("先打开作业页并自动划题，再载入答案。")
            return False
        path = os.path.abspath(os.path.expanduser(os.path.expandvars(str(path))))
        if not os.path.exists(path):
            self._warn("答案文件不存在：%s" % path)
            return False
        try:
            with open(path, "r", encoding="utf-8-sig") as fp:
                obj = json.load(fp)
        except Exception:
            self._warn("答案 JSON 读取失败：\n" + traceback.format_exc())
            return False
        payload = obj.get("answers") if isinstance(obj, dict) and \
            isinstance(obj.get("answers"), (dict, list)) else obj
        questions = self._questions_full()
        try:
            matches, warnings, _unmatched, _missing = worksheet.normalize_answers(
                payload, questions)
        except Exception:
            self._warn("答案格式不合法：\n" + traceback.format_exc())
            return False
        self.answers = {str(qid): str(text) for qid, text in matches.items()}
        self._sync_editor_from_current()
        self._update_status()
        self._schedule_live_preview(True, 80)
        self.log("已载入答案：%s（匹配 %d 题）"
                 % (os.path.basename(path), len(self.answers)))
        for w in warnings[:8]:
            self.log("[warning] " + str(w))
        return True

    def _default_answers_path(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        return os.path.join(OUT_DIR,
                            "做题答案_%s.json"
                            % datetime.now().strftime("%Y%m%d_%H%M%S"))

    def _default_generate_path(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        return os.path.join(OUT_DIR,
                            "做题窗口_%s.pdf"
                            % datetime.now().strftime("%Y%m%d_%H%M%S"))

    def _dlg_open_answers(self):
        try:
            path = filedialog.askopenfilename(
                parent=self.root, title="打开答案 JSON",
                initialdir=OUT_DIR,
                filetypes=[("答案 JSON", "*.json"), ("所有文件", "*.*")])
        except Exception:
            return
        if path:
            self.load_answers(path)

    def _dlg_save_answers(self):
        try:
            path = filedialog.asksaveasfilename(
                parent=self.root, title="保存答案 JSON",
                defaultextension=".json", initialdir=OUT_DIR,
                initialfile="做题答案_%s.json"
                            % datetime.now().strftime("%Y%m%d_%H%M%S"),
                filetypes=[("答案 JSON", "*.json")])
        except Exception:
            return
        if path:
            self.save_answers(path)

    # ------------------------------------------------------------ 生成（手动）
    # ------------------------------------------------------------ AI 自动作答
    def _llm_modules(self):
        import ai_prompt
        import llm_client
        return llm_client, ai_prompt

    def _dlg_ai_settings(self):
        """AI 模型设置：base_url / 模型名 / API Key（存 llm.json）。"""
        try:
            llm_client, _ai_prompt = self._llm_modules()
        except Exception as e:
            self._warn("AI 模块不可用：%s" % e)
            return
        cfg = llm_client.config()
        top = tk.Toplevel(self.root)
        top.title("AI 模型设置")
        top.transient(self.root)
        top.resizable(False, False)
        try:
            top.attributes("-topmost", True)
        except Exception:
            pass
        frm = ttk.Frame(top, padding=14)
        frm.pack(fill="both", expand=True)
        ttk.Label(frm, text="接口地址（OpenAI 兼容，DeepSeek 默认）").grid(
            row=0, column=0, sticky="w", pady=(0, 2))
        var_base = tk.StringVar(value=cfg.get("base_url") or "")
        ttk.Entry(frm, textvariable=var_base, width=52).grid(
            row=1, column=0, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="模型名").grid(row=2, column=0, sticky="w",
                                          pady=(0, 2))
        var_model = tk.StringVar(value=cfg.get("model") or "")
        ttk.Entry(frm, textvariable=var_model, width=52).grid(
            row=3, column=0, sticky="we", pady=(0, 8))
        ttk.Label(frm, text="API Key（保存到程序目录 llm.json，不要外传）").grid(
            row=4, column=0, sticky="w", pady=(0, 2))
        var_key = tk.StringVar(value=cfg.get("api_key") or "")
        ttk.Entry(frm, textvariable=var_key, width=52, show="*").grid(
            row=5, column=0, sticky="we", pady=(0, 10))
        ttk.Label(frm, text="本地 Ollama 可免 Key：http://127.0.0.1:11434/v1",
                  style="Muted.TLabel").grid(row=6, column=0, sticky="w")
        btns = ttk.Frame(frm)
        btns.grid(row=7, column=0, sticky="e", pady=(10, 0))

        def save():
            try:
                path = llm_client.save_config(var_base.get(), var_key.get(),
                                              var_model.get())
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=top)
                return
            self.log("AI 配置已保存：%s" % path)
            messagebox.showinfo("已保存", "AI 配置已保存到：\n%s" % path,
                                parent=top)
            top.destroy()

        ttk.Button(btns, text="保存", command=save, style="Accent.TButton").pack(
            side="left", padx=4)
        ttk.Button(btns, text="取消", command=top.destroy).pack(side="left")
        top.grab_set()
        try:
            top.focus_force()
        except Exception:
            pass

    def ai_solve(self):
        """一键 AI 作答：划好题后自动调模型，把答案填进每题编辑框（不写文件）。"""
        if self._busy:
            self.log("正在忙，稍后再点。")
            return
        if not self.spec or not self.questions:
            self._warn("先打开作业页并自动划题，再点 AI 自动作答。")
            return
        try:
            llm_client, ai_prompt = self._llm_modules()
        except Exception as e:
            self._warn("AI 模块不可用：%s" % e)
            return
        if not llm_client.available():
            if messagebox.askyesno(
                    "还没有配置 AI 模型",
                    "没有检测到答题模型（API Key）。\n\n"
                    "现在去配置吗？\n（DeepSeek/OpenAI 兼容接口；本地 Ollama 可免 Key）",
                    parent=self.root):
                self._dlg_ai_settings()
            return
        cfg = llm_client.public_config()
        self._set_busy(True, "AI 正在作答…（%s）" % cfg.get("model"))
        self.log("AI 作答中：%s @ %s" % (cfg.get("model"), cfg.get("base_url")))

        import queue
        import threading

        self._ai_queue = queue.Queue()

        def worker():
            try:
                got = llm_client.solve_worksheet(
                    self.spec, ai_prompt.worksheet_prompt(self.spec))
                self._ai_queue.put((got, None))
            except Exception as e:                       # noqa: BLE001
                self._ai_queue.put((None, e))

        threading.Thread(target=worker, daemon=True, name="ai-solve").start()
        self.root.after(80, self._ai_poll)

    def _ai_poll(self):
        """主线程轮询 AI 结果：跨线程只传 queue，避免 tkinter 线程安全问题。"""
        q = getattr(self, "_ai_queue", None)
        if q is None:
            return
        try:
            got, err = q.get_nowait()
        except Exception:
            self.root.after(80, self._ai_poll)
            return
        self._ai_queue = None
        self._ai_done(got, err)

    def _ai_done(self, got, err):
        self._set_busy(False)
        if err is not None:
            hint = getattr(err, "hint", None)
            msg = str(err) + (("\n\n建议：" + hint) if hint else "")
            self.log("AI 作答失败：" + str(err))
            self._warn("AI 作答失败：\n" + msg)
            self._update_status()
            return
        answers = (got or {}).get("answers") or {}
        qs = [{"id": q.get("id"), "number": q.get("number")}
              for q in self.questions]
        try:
            matches, _w, _u, _m = worksheet.normalize_answers(answers, qs)
        except Exception:
            matches = {str(k): v for k, v in answers.items()}
        if not matches:
            self._warn("AI 返回的答案没有匹配到题目，请检查题号。")
            self._update_status()
            return
        filled = 0
        for qid, text in matches.items():
            if isinstance(text, str) and text.strip():
                self.answers[str(qid)] = text
                filled += 1
        self._rebuild_question_views()
        # 注意：不能走 select_question()——它会 _commit_editor()，
        # 把 AI 刚写入的当前题答案又用（旧的）空编辑器内容覆盖掉。
        try:
            idx = self.current if self.current is not None else 0
            if 0 <= idx < len(self.questions):
                self.current = int(idx)
                q = self.questions[self.current]
                self.page = int(q.get("page") or 0)
                self.generated_mode = False
                self._generated_img = None
                self.qlist.select(self.current)
                self._sync_editor_from_current()
                self._redraw_canvas()
        except Exception:
            pass
        self._update_status()
        self._schedule_live_preview(True, 80)
        meta = (got or {}).get("meta") or {}
        self.log("AI 作答完成：%d 题（%s ms，模型 %s）"
                 % (filled, meta.get("elapsed_ms"), meta.get("model")))
        try:
            self.status_label.configure(
                text="AI 已答 %d 题 · 可手动修改后点「生成手写作答」" % filled)
        except Exception:
            pass

    def _dlg_generate(self):
        """「另存为…」：临时选位置，并把选择回填到界面输出设置。"""
        if not self.spec or not self.questions:
            self._warn("先打开作业页并自动划题。")
            return
        try:
            path = filedialog.asksaveasfilename(
                parent=self.root, title="生成手写作答（另存 PDF）",
                defaultextension=".pdf",
                initialdir=(self.var_outdir.get() or OUT_DIR),
                initialfile=(self.var_outname.get() or self._stamped_name()),
                filetypes=[("PDF", "*.pdf")])
        except Exception:
            return
        if path:
            path = os.path.abspath(path)
            self.var_outdir.set(os.path.dirname(path))
            self.var_outname.set(os.path.basename(path))
            self._outname_custom = True
            self.generate(out_pdf=path)

    def _build_overrides(self, matched_ids):
        """按 spec 顺序给出每道「有答案」题的字号/对齐覆盖。"""
        overrides = []
        for pg in (self.spec or {}).get("pages") or []:
            for q in pg.get("questions") or []:
                qid = str(q.get("id"))
                if qid not in matched_ids:
                    continue
                size = self.sizes.get(qid)
                align = self.aligns.get(qid)
                custom_size = (size is not None
                               and abs(float(size) - DEFAULT_SIZE_PT) > 1e-6)
                custom_align = (align in ("center", "right"))
                overrides.append((float(size) if custom_size else None,
                                  align if custom_align else None))
        return overrides

    def generate(self, out_pdf=None, preview_png=None):
        """用户主动生成手写作答：调用 worksheet.render_answers 写 PDF。"""
        if self._busy:
            self.log("正在忙，稍后再点。")
            return None
        if not self.spec or not self.questions:
            self._warn("先打开作业页并自动划题。")
            return None
        self._commit_editor()
        answers = {qid: text for qid, text in self.answers.items()
                   if (text or "").strip()}
        if not answers:
            self._warn("还没有填写答案：先在右侧编辑框里写几题，再点生成。")
            return None
        if not out_pdf:
            out_pdf = self._default_generate_path()
        out_pdf = os.path.abspath(os.path.expanduser(os.path.expandvars(
            str(out_pdf))))
        try:
            folder = os.path.dirname(out_pdf)
            if folder:
                os.makedirs(folder, exist_ok=True)
        except Exception:
            self._warn("输出目录不可写：%s" % os.path.dirname(out_pdf))
            return None
        self._set_busy(True, "正在生成手写作答（%d 题）…" % len(answers))
        try:
            overrides = self._build_overrides(set(answers))
            shim = (make_page_model_shim(overrides)
                    if any(s is not None or a is not None
                           for s, a in overrides) else None)
            pdf_path, warnings, coverage = worksheet.render_answers(
                self.spec, answers, out_pdf=out_pdf, page_model=shim)
        except Exception:
            tb = traceback.format_exc()
            self._set_busy(False)
            self._warn("生成失败：\n" + tb)
            self._update_status()
            return None
        self.generated_path = pdf_path or out_pdf
        self.last_coverage = coverage
        self.log("已生成 PDF：%s（%d/%d 题有答案，覆盖率 %.0f%%）"
                 % (self.generated_path, coverage.get("answered", 0),
                    coverage.get("questions", 0),
                    100.0 * float(coverage.get("coverage") or 0.0)))
        for w in warnings[:20]:
            self.log("[warning] " + str(w))
        # 预览：渲染 PDF 里「第一道有答案的题」所在页
        preview_page = self._first_answered_page(answers)
        self.page = preview_page
        try:
            self._generated_img = self._render_pdf_page_image(
                self.generated_path, preview_page)
        except Exception:
            self._generated_img = None
            self.log("[warning] 生成预览失败：\n" + traceback.format_exc())
        # 另存一张预览 PNG（可选，便于分享/核对；这是用户主动生成后的产物）
        if preview_png is None:
            preview_png = os.path.splitext(self.generated_path)[0] + "_预览.png"
        if preview_png and self._generated_img is not None:
            try:
                folder = os.path.dirname(os.path.abspath(preview_png))
                if folder:
                    os.makedirs(folder, exist_ok=True)
                self._generated_img.save(preview_png)
                self.preview_png_path = preview_png
                self.log("预览 PNG：%s" % preview_png)
            except Exception:
                self.log("[warning] 预览 PNG 保存失败：\n" + traceback.format_exc())
        self.generated_mode = True
        self._set_busy(False)
        self._redraw_canvas()
        self._update_status()
        return self.generated_path

    def _first_answered_page(self, answers):
        for pg in (self.spec or {}).get("pages") or []:
            for q in pg.get("questions") or []:
                if str(q.get("id")) in answers:
                    return int(pg.get("page") or 0)
        return 0

    def _render_pdf_page_image(self, pdf_path, page_idx, dpi=110):
        if _fitz is None:
            raise RuntimeError("没有 PyMuPDF，无法渲染生成预览")
        doc = _fitz.open(pdf_path)
        try:
            idx = max(0, min(int(page_idx), len(doc) - 1))
            page = doc[idx]
            zoom = float(dpi) / 72.0
            pix = page.get_pixmap(matrix=_fitz.Matrix(zoom, zoom),
                                  alpha=False)
            return Image.frombytes("RGB", (pix.width, pix.height),
                                   pix.samples)
        finally:
            doc.close()


# ======================================================================
# 入口
# ======================================================================
def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="做题窗口.py",
        description="独立的做题窗口：打开作业页 → 自动划题 → 手改答案 → 手动生成手写作答")
    parser.add_argument("--open", default=None,
                        help="启动后自动打开的作业页（PDF/图片）")
    parser.add_argument("--numbers", default=None,
                        help='扫描件题号，逗号分隔：--numbers "4.2.1,4.2.2"')
    parser.add_argument("--page", default="all",
                        help="分析页：all（默认）或页号（从 0 开始）")
    parser.add_argument("--smoke", action="store_true",
                        help="打包自检：无窗口构建一次后退出（打包测试用）")
    parser.add_argument("--ai-smoke", default=None, metavar="PDF",
                        help="打包自检：打开作业→AI 自动作答→写结果文件后退出")
    parser.add_argument("--selftest", default=None, metavar="PDF",
                        help="打包自检：打开作业→填答案→生成手写作答→写结果后退出")
    args = parser.parse_args(argv if argv is not None else None)
    if args.selftest:
        root = tk.Tk()
        root.withdraw()
        app = WorksheetWindow(root)
        app.live_preview_on = False
        ok = app.load_source(args.selftest, numbers=args.numbers)
        pdf = None
        if ok:
            for q in app.questions:
                app.answers[str(q.get("id"))] = "自检答案 220/10 = 22"
            pdf = app.generate(out_pdf=os.path.join(
                APP_BASE, "selftest_手写作答.pdf"))
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False) else APP_BASE)
        with open(os.path.join(base, "selftest_result.txt"), "w",
                  encoding="utf-8") as f:
            f.write("LOAD=%s\nQUESTIONS=%d\nPDF=%s\nPDF_OK=%s\n"
                    % (bool(ok), len(app.questions), pdf or "",
                       bool(pdf and os.path.exists(pdf)
                            and os.path.getsize(pdf) > 1000)))
        try:
            root.destroy()
        except Exception:
            pass
        return 0 if (ok and pdf and os.path.exists(pdf)) else 1
    if args.ai_smoke:
        root = tk.Tk()
        root.withdraw()
        app = WorksheetWindow(root)
        app.live_preview_on = False
        app.load_source(args.ai_smoke, numbers=args.numbers)
        app.ai_solve()
        import time as _time
        t0 = _time.time()
        while app._busy and _time.time() - t0 < 180:
            root.update()
            _time.sleep(0.05)
        for _ in range(40):
            root.update()
            _time.sleep(0.02)
        filled = sum(1 for v in app.answers.values() if (v or "").strip())
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False) else HERE)
        with open(os.path.join(base, "smoke_ai.txt"), "w",
                  encoding="utf-8") as f:
            f.write("AI_FILLED=%d\\nTOTAL=%d" % (filled, len(app.questions)))
        try:
            root.destroy()
        except Exception:
            pass
        return 0 if filled else 1
    root = tk.Tk()
    if args.smoke:
        root.withdraw()
        app = WorksheetWindow(root)
        app.live_preview_on = False
        root.update_idletasks()
        root.update()
        try:
            root.destroy()
        except Exception:
            pass
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False) else HERE)
        try:
            with open(os.path.join(base, "smoke_ws.txt"), "w",
                      encoding="utf-8") as f:
                f.write("SMOKE OK")
        except Exception:
            pass
        print("SMOKE OK")
        return 0
    app = WorksheetWindow(root)
    if args.open:
        root.after(80, lambda: app.load_source(args.open,
                                               numbers=args.numbers,
                                               page=args.page))
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
