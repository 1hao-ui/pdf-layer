# -*- coding: utf-8 -*-
"""theme.py — 「手写体生成器」视觉主题模块（沙滩主题）。

设计目标：中文界面、干净克制、沙滩配色。浅色是「晴日海滩」（沙 / 泡沫 / 礁湖青 /
珊瑚），深色是「夜海」（深海蓝 / 月光青）。纯标准库（tkinter / ttk），零第三方
依赖；没有 tkinter 或没有可用显示时依然可以 import，`apply_theme()` 只返回设计
令牌（tokens），不会抛异常。

快速接入
--------
    import tkinter as tk
    from tkinter import ttk
    import theme

    theme.set_dpi_aware()                      # 必须在创建 Tk() 之前调用
    root = tk.Tk()
    tokens = theme.apply_theme(root)           # 浅色（默认）
    # 深色：tokens = theme.apply_theme(root, dark=True)

    pal = tokens["PALETTE"]                    # 色板 dict
    st = tokens["STYLES"]                      # 样式名 dict
    ttk.Frame(root, style=st["card"])          # "Card.TFrame"
    ttk.Button(root, text="渲染", style=st["accent_button"])

    # 无窗口环境 / 单元测试：只取令牌，不碰 ttk
    tokens = theme.apply_theme(None)
    tokens = theme.apply_theme(None, dark=True)

对外接口
--------
    PALETTE_LIGHT / PALETTE_DARK : dict        两套完整色板
    PALETTE                      : dict        浅色色板别名（默认）
    SIZES / SPACING / RADIUS     : dict        字号 / 间距 / 圆角令牌
    STYLES                       : dict        语义键 → ttk 样式名
    apply_theme(root=None, dark=False) -> dict 配置 ttk + tk，返回 tokens
    tokens_for(dark=False, root=None) -> dict  只组装令牌（等于 apply_theme(None)）
    available(dark=False) -> bool              当前环境能否真正套用主题
    set_dpi_aware() -> bool                    进程级 DPI 感知（Windows；其它平台返回 False）
    resolve_font_family(root=None, candidates=None) -> str   字体回退选择

ttk 限制（详见 docs/界面设计规范.md）
-------------------------------------
    · ttk 没有真正的圆角/阴影/渐变；"卡片感" = 白底 + 1px 浅边框 + 留白，
      RADIUS 令牌只给 Canvas / PIL 效果图参考。
    · Windows 默认 vista/xpnative 主题会忽略大量颜色配置，所以本模块统一改用
      "clam" 基础主题（可用 theme.theme_use() 查当前主题）。
    · 样式名区分大小写；"Card.Muted.TLabel" 会继承 "Muted.TLabel"，只覆盖背景色。
    · 小数磅值 Tk 不接受，10.5pt 这类值会换算成等值像素（96dpi 下 -14px）。
"""

from __future__ import annotations

import math
import os
import random

__all__ = [
    "PALETTE_LIGHT",
    "PALETTE_DARK",
    "PALETTE",
    "SIZES",
    "SPACING",
    "RADIUS",
    "STYLES",
    "FONT_CANDIDATES",
    "MONO_CANDIDATES",
    "TKINTER_OK",
    "apply_theme",
    "tokens_for",
    "available",
    "set_dpi_aware",
    "resolve_font_family",
    "BeachStrip",
    "paint_sea",
]

__version__ = "2.0.0"

# --------------------------------------------------------------------------- tkinter 可选导入
try:  # pragma: no cover - 取决于运行环境
    import tkinter as _tk
    from tkinter import ttk as _ttk
    import tkinter.font as _tkfont

    TKINTER_OK = True
except Exception:  # 无 tkinter 的解释器（精简版/嵌入式）也能 import 本模块
    _tk = None
    _ttk = None
    _tkfont = None
    TKINTER_OK = False


# --------------------------------------------------------------------------- 设计令牌：色板
# 命名是一套「语义色」，不写死控件：改主题只改这里。
#
# 主题：沙滩（Beach）—— 浅色是「晴日海滩」，深色是「夜海」。
#   沙   #F6EDDD  页面底色（晒暖的沙）
#   泡沫 #FFFCF4  卡片 / 面板（浪花白）
#   海   #0C7F8B  主色（礁湖青），只给主操作与选中
#   珊瑚 #DE6A55  警示 / 破坏性操作
#   浮木 #322C24  正文文字（深木色，比纯黑软）
#   画布 #D5E7E6  预览区（浅海），衬托暖白纸面
PALETTE_LIGHT = {
    # 底层（大面）
    "bg": "#F6EDDD",            # 页面背景（窗口底色，暖沙）
    "card": "#FFFCF4",          # 卡片 / 面板（浪花白）
    "card_alt": "#F8EFDE",      # 卡片内的浅色块、表头、只读底
    "sidebar": "#F1E4CF",       # 左侧栏底色（沙丘，卡片叠在上面）
    "toolbar": "#FFFCF4",       # 顶栏
    "panel": "#FFFCF4",         # 工具条/状态栏底色（兼容 gui 的 panel 键）
    "log": "#FFFCF4",           # 日志栏
    "canvas": "#D5E7E6",        # 画布工作区（浅海，衬托暖白纸）
    "canvas_dot": "#BFD6D3",    # 画布点阵网格（比 canvas 略深，别抢纸）
    "track": "#EDE0CA",         # 进度条 / 滑杆 / 空状态轨道
    # 线
    "border": "#E7D9C2",        # 1px 常规边框
    "border_strong": "#D6C4A6",  # 需要强调的边框（输入框、次级按钮）
    # 文字
    "text": "#322C24",          # 正文（浮木色）
    "muted": "#7B7061",         # 次要文字 / 说明
    "faint": "#A99C86",         # 占位、禁用、极次要
    "on_accent": "#FFFFFF",     # 主色/深色块上的文字
    # 品牌与状态
    "accent": "#0C7F8B",        # 主色（礁湖青）
    "accent_hover": "#0A6C77",
    "accent_pressed": "#085A64",
    "accent_soft": "#DBEFEE",   # 主色浅底（选中、标签、强调块）
    "success": "#2E9B6B",
    "success_soft": "#E2F2E7",
    "danger": "#DE6A55",        # 警示 / 破坏性操作（珊瑚）
    "danger_hover": "#C8553F",
    "danger_soft": "#FAE6DE",
    # 交互态（selection 是"选中底色"，配白字；gui 的列表/输入框选中都用它）
    "hover": "#F2E6D2",
    "pressed": "#E9DAC1",
    "selection": "#0C7F8B",
    # 纸张（A4 预览）：纸永远是白的，纸上的字/字迹要固定用 paper_ink，
    # 不能跟着深色模式变亮（否则深色下白纸上的浅字看不见）。
    "paper": "#FFFDF8",         # 暖白纸（不是纯白，像晒过的纸）
    "paper_edge": "#E4D6BD",
    "paper_ink": "#27231C",
    "shadow": "#6A5B45",        # 阴影基色（Canvas 画阴影时自己加 alpha）
}

# 深色：夜海 —— 月光下的沙滩：暖黑沙 + 深海青 + 月光青
# 注意别做成冷蓝灰：夜景的「沙」要暖，「海」要深，主色只留一点点月光。
PALETTE_DARK = {
    "bg": "#1D1712",            # 夜里的沙（暖黑）
    "card": "#2A2118",          # 浮木卡片
    "card_alt": "#241C15",
    "sidebar": "#17110D",       # 背光的沙丘
    "toolbar": "#241C16",
    "panel": "#241C16",
    "log": "#241C16",
    "canvas": "#0B2A30",        # 深海夜
    "canvas_dot": "#234045",
    "track": "#3A2E22",
    "border": "#3A2E22",
    "border_strong": "#4E3E2C",
    "text": "#F4E9D6",          # 月光下的沙白
    "muted": "#B7A68C",
    "faint": "#8C7B63",
    "on_accent": "#FFFFFF",
    "accent": "#5CC9C4",        # 月光青
    "accent_hover": "#7ADBD5",
    "accent_pressed": "#45B0AB",
    "accent_soft": "#1B3A3A",
    "success": "#6FD39B",
    "success_soft": "#17352A",
    "danger": "#F08A78",
    "danger_hover": "#E0695A",
    "danger_soft": "#3D2620",
    "hover": "#32271C",
    "pressed": "#2A2118",
    "selection": "#24504F",
    "paper": "#FFF8EA",         # 纸永远是暖白的（深色界面里更亮眼）
    "paper_edge": "#3A2E22",
    "paper_ink": "#241F17",
    "shadow": "#000000",
}

PALETTE = PALETTE_LIGHT  # 默认浅色


# --------------------------------------------------------------------------- 设计令牌：字号 / 间距 / 圆角
# 字号单位 pt（Tk 正数 = 磅值）；小数磅值会换算成等值像素，见 _size_value()。
SIZES = {
    "h1": 15.0,        # 页面级标题 / 顶栏主标题
    "section": 10.5,   # 卡片标题（SectionHeader）
    "body": 10.0,      # 正文
    "value": 10.0,     # 数值（加粗，和正文同号）
    "muted": 9.0,      # 次要说明
    "mono": 9.0,       # 日志/数值等宽字
    "tab": 10.0,       # Notebook 标签
    "button": 10.0,    # 按钮文字
}

# 间距基线：8 / 6（控件内边距），卡片留白 12/10，行距 6。
SPACING = {
    "xs": 2,
    "sm": 4,
    "md": 6,
    "lg": 8,
    "xl": 12,
    "xxl": 16,
    "xxxl": 24,
    "ctrl_pad": (8, 6),        # 输入类控件内边距（左右 / 上下）
    "btn_pad": (12, 6),        # 按钮内边距（中文双字按钮左右略放宽）
    "card_pad": (12, 10),      # 卡片内边距
    "row_gap": 6,              # 卡片内行距
    "section_gap": 10,         # 卡片与卡片之间
    "field_label_gap": 8,      # 标签到控件的间距
}

# ttk 不做圆角，这里只给 Canvas / PIL 效果图统一参考值。
RADIUS = {"window": 12, "card": 8, "control": 6, "chip": 4, "page": 3}


# --------------------------------------------------------------------------- 语义样式名
# 语义键 → ttk 样式名。gui 里尽量用 tokens["STYLES"]["card"]，不要写死字符串。
STYLES = {
    # 容器
    "frame": "TFrame",
    "card": "Card.TFrame",
    "sidebar": "Sidebar.TFrame",
    "toolbar": "Toolbar.TFrame",
    "log": "Log.TFrame",
    "card_box": "Card.TLabelframe",          # 带标题的卡片（推荐）
    "card_title": "Card.TLabelframe.Label",
    # 文字（页面底色上）
    "label": "TLabel",
    "h1": "H1.TLabel",
    "section": "SectionHeader.TLabel",
    "muted": "Muted.TLabel",
    "value": "Value.TLabel",
    "mono": "Mono.TLabel",
    "brand": "Brand.TLabel",                 # 顶栏品牌名（主色加粗）
    "chip": "Chip.TLabel",                   # 小胶囊标签（主色浅底 + 主色字）
    # 文字（容器内配套变体，背景色跟着容器走）
    "card_label": "Card.TLabel",
    "card_section": "Card.SectionHeader.TLabel",
    "card_muted": "Card.Muted.TLabel",
    "card_value": "Card.Value.TLabel",
    "sidebar_label": "Sidebar.TLabel",
    "sidebar_section": "Sidebar.SectionHeader.TLabel",
    "sidebar_muted": "Sidebar.Muted.TLabel",
    "toolbar_label": "Toolbar.TLabel",
    "toolbar_h1": "Toolbar.H1.TLabel",
    "log_label": "Log.TLabel",
    "log_mono": "Log.Mono.TLabel",
    # 按钮
    "button": "TButton",                     # 次级（白底 + 边框）
    "accent_button": "Accent.TButton",       # 主操作（如「渲染」）
    "ghost_button": "Ghost.TButton",         # 低强调（无边框、hover 才显色）
    "danger_button": "Danger.TButton",       # 破坏性操作
    "tool_button": "Tool.TButton",           # 顶栏工具按钮（更紧凑）
    # 输入
    "entry": "TEntry",
    "combobox": "TCombobox",
    "spinbox": "TSpinbox",
    # 其它
    "notebook": "TNotebook",
    "tab": "TNotebook.Tab",
    "progress": "Horizontal.TProgressbar",
    "scale": "TScale",
    "check": "TCheckbutton",
    "radio": "TRadiobutton",
    "separator": "TSeparator",
    "scrollbar": "TScrollbar",
}

FONT_CANDIDATES = (
    "Microsoft YaHei UI",   # Win8.1+ 微软雅黑 UI，首选
    "Microsoft YaHei",      # 老系统 / 部分软件带的雅黑
    "PingFang SC",          # macOS
    "Noto Sans CJK SC",     # Linux
    "SimSun",               # 宋体兜底
    "Segoe UI",             # 纯西文兜底
    "TkDefaultFont",
)
MONO_CANDIDATES = ("Cascadia Mono", "Consolas", "JetBrains Mono", "Courier New", "TkFixedFont")

_DPI96_SCALE = 96.0 / 72.0   # 96dpi 下 1pt = 1.333px
_THEME_BASE = "clam"         # 唯一能稳定改色系的 ttk 基础主题


# --------------------------------------------------------------------------- 字体
def _scaling(root=None):
    """当前 Tk 的 scaling（每磅多少像素）。默认 96dpi = 1.333。"""
    if root is not None:
        try:
            value = float(root.tk.call("tk", "scaling"))
            if value > 0:
                return value
        except Exception:
            pass
    return _DPI96_SCALE


def _size_value(root, pt):
    """pt → Tk 字号。

    Tk 只接受整数磅值；整数磅用正值（由 Tk 按 DPI 自动缩放），
    小数磅（如 10.5pt）改写成等值像素的负值。
    """
    pt = float(pt)
    if abs(pt - round(pt)) < 1e-9:
        return int(round(pt))
    return -max(1, int(round(pt * _scaling(root))))


def _font_tuple(root, family, pt, weight=None):
    parts = []
    if family:
        parts.append(family)
    parts.append(_size_value(root, pt))
    if weight and weight not in ("normal", "regular", None):
        parts.append(weight)
    return tuple(parts)


def resolve_font_family(root=None, candidates=None):
    """按候选顺序挑一个系统里真实存在的字体族。

    没有 tkinter / 没有 root 时直接返回首选（假定 Windows 目标机）。
    都找不到时返回 "TkDefaultFont"（Tk 会退回系统默认字体，不会崩）。
    """
    candidates = tuple(candidates or FONT_CANDIDATES)
    if root is not None and _tkfont is not None:
        try:
            families = set(_tkfont.families(root))
            for name in candidates:
                if name in families:
                    return name
        except Exception:
            pass
    for name in candidates:
        if name not in ("TkDefaultFont", "TkFixedFont"):
            return name
    return candidates[-1] if candidates else "Microsoft YaHei UI"


def _build_fonts(root, family, mono_family):
    def font(pt, weight=None, fam=None):
        return _font_tuple(root, fam or family, pt, weight)

    return {
        "family": family,
        "mono_family": mono_family,
        "h1": font(SIZES["h1"], "bold"),
        "section": font(SIZES["section"], "bold"),
        "body": font(SIZES["body"]),
        "value": font(SIZES["value"], "bold"),
        "muted": font(SIZES["muted"]),
        "mono": font(SIZES["mono"], fam=mono_family),
        "tab": font(SIZES["tab"]),
        "button": font(SIZES["button"]),
    }


# --------------------------------------------------------------------------- tokens
def tokens_for(dark=False, root=None):
    """只组装设计令牌，不修改任何 Tk/ttk 状态（apply_theme(None) 就等于它）。"""
    palette = dict(PALETTE_DARK if dark else PALETTE_LIGHT)
    family = resolve_font_family(root)
    mono_family = resolve_font_family(root, MONO_CANDIDATES)
    fonts = _build_fonts(root, family, mono_family)
    tk_tokens = {
        "canvas_bg": palette["canvas"],     # tk.Canvas(bg=...)
        "text_bg": palette["card"],         # tk.Text(bg=...)
        "log_text_bg": palette["log"],
        "input_bg": palette["card"],
        "select_bg": palette["selection"],
        "select_fg": palette["on_accent"],
        "paper": palette["paper"],
        "paper_ink": palette["paper_ink"],  # 白纸上画字/段框标题时用这个
    }
    styles = dict(STYLES)
    tokens = {
        "PALETTE": palette,
        "palette": palette,          # 小写别名，图省事用
        "DARK": bool(dark),
        "FONT_FAMILY": family,
        "FONT_MONO": mono_family,
        "FONTS": fonts,
        "SIZES": dict(SIZES),
        "SPACING": dict(SPACING),
        "RADIUS": dict(RADIUS),
        "STYLES": styles,
        "styles": styles,
        "TK": tk_tokens,
        "SCALING": _scaling(root),
        "THEME": _THEME_BASE,
        "THEME_MODULE": __name__,
        "VERSION": __version__,
    }
    return tokens


# --------------------------------------------------------------------------- DPI
def set_dpi_aware():
    """进程级 DPI 感知（Windows 8.1+ shcore，失败退回 user32）。

    必须在创建 Tk() 之前调用；非 Windows 或调用失败时返回 False，不抛异常。
    """
    if os.name != "nt":
        return False
    try:
        import ctypes
    except Exception:
        return False
    # 1 = PROCESS_SYSTEM_DPI_AWARE，高 DPI 下文字不发虚
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
        return True
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
        return True
    except Exception:
        pass
    return False


def available(dark=False):
    """当前环境能否真正套用主题（有 tkinter 且能创建窗口）。"""
    if not TKINTER_OK:
        return False
    try:
        if getattr(_tk, "_default_root", None) is not None:
            return True
    except Exception:
        pass
    try:
        probe = _tk.Tk()
        try:
            probe.withdraw()
        except Exception:
            pass
        probe.destroy()
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- tk 侧（经典控件 + 命名字体）
def _apply_tk_defaults(root, palette, fonts, family, mono_family):
    """让 tk 经典控件（Canvas/Text/Listbox/Button）也跟上主题。"""
    try:
        root.configure(bg=palette["bg"])
    except Exception:
        pass

    # 命名字体：tk 控件默认字体跟着变
    if _tkfont is not None:
        wanted = (
            ("TkDefaultFont", fonts["body"], "normal"),
            ("TkTextFont", fonts["body"], "normal"),
            ("TkMenuFont", fonts["body"], "normal"),
            ("TkCaptionFont", fonts["body"], "normal"),
            ("TkSmallCaptionFont", fonts["muted"], "normal"),
            ("TkIconFont", fonts["body"], "normal"),
            ("TkTooltipFont", fonts["muted"], "normal"),
            ("TkHeadingFont", fonts["section"], "bold"),
            ("TkFixedFont", fonts["mono"], "normal"),
        )
        for name, spec, weight in wanted:
            try:
                named = _tkfont.nametofont(name, root=root)
                if len(spec) >= 2 and isinstance(spec[1], int):
                    named.configure(family=family if name != "TkFixedFont" else mono_family,
                                    size=spec[1], weight=weight)
            except Exception:
                continue

    # 经典控件配色（只影响之后创建的控件，不影响 ttk）
    options = (
        ("*Background", palette["card"]),
        ("*Foreground", palette["text"]),
        ("*Font", fonts["body"]),
        ("*selectBackground", palette["accent"]),
        ("*selectForeground", palette["on_accent"]),
        ("*insertBackground", palette["text"]),
        ("*highlightColor", palette["accent"]),
        ("*highlightBackground", palette["border"]),
        ("*troughColor", palette["track"]),
        ("*activeBackground", palette["hover"]),
        ("*Listbox.background", palette["card"]),
        ("*Listbox.foreground", palette["text"]),
        ("*Text.background", palette["card"]),
        ("*Text.foreground", palette["text"]),
        ("*Text.insertBackground", palette["text"]),
        ("*Canvas.background", palette["canvas"]),
        ("*TCombobox*Listbox.background", palette["card"]),
        ("*TCombobox*Listbox.foreground", palette["text"]),
        ("*TCombobox*Listbox.selectBackground", palette["accent"]),
        ("*TCombobox*Listbox.selectForeground", palette["on_accent"]),
    )
    for pattern, value in options:
        try:
            root.option_add(pattern, value)
        except Exception:
            continue


# --------------------------------------------------------------------------- ttk 侧
def _configure_styles(style, palette, fonts):
    """配置全部命名 ttk 样式。单条失败不影响其它条（不抛异常）。"""
    pal = palette
    accent = pal["accent"]
    accent_hover = pal["accent_hover"]
    accent_pressed = pal["accent_pressed"]
    accent_soft = pal["accent_soft"]
    card = pal["card"]
    body = fonts["body"]
    section = fonts["section"]
    h1 = fonts["h1"]
    muted_f = fonts["muted"]
    value_f = fonts["value"]
    mono_f = fonts["mono"]

    def c(name, **kw):
        try:
            style.configure(name, **kw)
        except Exception:
            pass

    def m(name, **kw):
        try:
            style.map(name, **kw)
        except Exception:
            pass

    # 根样式：所有 ttk 控件的兜底
    c(".", background=pal["bg"], foreground=pal["text"], font=body,
      bordercolor=pal["border"], troughcolor=pal["track"],
      focuscolor=accent, selectbackground=pal["accent"],
      selectforeground=pal["on_accent"], insertcolor=pal["text"])

    # ---- 容器 ------------------------------------------------------------
    c("TFrame", background=pal["bg"], borderwidth=0, relief="flat")
    c("Card.TFrame", background=card, borderwidth=0, relief="flat")
    c("Sidebar.TFrame", background=pal["sidebar"], borderwidth=0, relief="flat")
    c("Toolbar.TFrame", background=pal["toolbar"], borderwidth=0, relief="flat")
    c("Log.TFrame", background=pal["log"], borderwidth=0, relief="flat")
    c("Status.TFrame", background=pal["toolbar"], borderwidth=0, relief="flat")
    c("Status.TLabel", background=pal["toolbar"], foreground=pal["text"], font=body)
    c("Status.Muted.TLabel", background=pal["toolbar"], foreground=pal["muted"], font=muted_f)
    # 可拖动分栏（ttk.Panedwindow）：默认 gray95 会露出一条灰边，要压成页面色
    c("TPanedwindow", background=pal["bg"], borderwidth=0, relief="flat")
    c("Sash", background=pal["border"], gripcount=0, sashrelief="flat")

    # 带标题的卡片：白底 + 1px 浅边框 + 内留白（ttk 无真圆角）
    c("Card.TLabelframe", background=card, bordercolor=pal["border"],
      borderwidth=1, relief="solid", padding=SPACING["card_pad"])
    c("Card.TLabelframe.Label", background=card, foreground=pal["text"],
      font=section, padding=(0, 0, 6, 6), borderwidth=0)
    c("TLabelframe", background=card, bordercolor=pal["border"],
      borderwidth=1, relief="solid")
    c("TLabelframe.Label", background=card, foreground=pal["text"], font=section)

    # ---- 文字 ------------------------------------------------------------
    c("TLabel", background=pal["bg"], foreground=pal["text"], font=body)
    c("H1.TLabel", background=pal["bg"], foreground=pal["text"], font=h1)
    c("SectionHeader.TLabel", background=pal["bg"], foreground=pal["text"], font=section)
    c("Muted.TLabel", background=pal["bg"], foreground=pal["muted"], font=muted_f)
    c("Value.TLabel", background=pal["bg"], foreground=pal["text"], font=value_f)
    c("Mono.TLabel", background=pal["bg"], foreground=pal["text"], font=mono_f)

    # 容器内配套变体：只覆盖背景（自动继承上面的字色/字号）
    container_bg = {
        "Card": card,
        "Sidebar": pal["sidebar"],
        "Toolbar": pal["toolbar"],
        "Status": pal["toolbar"],
        "Log": pal["log"],
    }
    variants = (
        "TLabel",
        "H1.TLabel",
        "SectionHeader.TLabel",
        "Muted.TLabel",
        "Value.TLabel",
        "Mono.TLabel",
        "TCheckbutton",
        "TRadiobutton",
    )
    for prefix, bg in container_bg.items():
        for suffix in variants:
            c("%s.%s" % (prefix, suffix), background=bg)

    # 顶栏品牌名 / 小胶囊标签（不跟容器变体走）
    c("Brand.TLabel", background=pal["toolbar"], foreground=accent, font=h1)
    c("Chip.TLabel", background=accent_soft, foreground=accent, font=muted_f,
      padding=(7, 2))

    # 卡片内标题用 "Card.SectionHeader.TLabel"，深色下不会露出灰底

    # ---- 按钮 ------------------------------------------------------------
    # 次级按钮：白底 / 1px 边框 / hover 变浅灰
    # 注意：clam 的 Button.border 在 relief=flat 下不画边框，必须用 solid 才是「1px 细边」
    c("TButton", background=card, foreground=pal["text"], font=body,
      bordercolor=pal["border_strong"], lightcolor=pal["border_strong"],
      darkcolor=pal["border_strong"], borderwidth=1, relief="solid",
      padding=SPACING["btn_pad"], focusthickness=0, focuscolor=accent,
      anchor="center")
    m("TButton",
      background=[("pressed", pal["pressed"]), ("active", pal["hover"]),
                  ("disabled", pal["card_alt"])],
      foreground=[("disabled", pal["faint"])],
      bordercolor=[("focus", accent), ("active", pal["border_strong"])],
      lightcolor=[("pressed", pal["border_strong"]), ("active", pal["border_strong"])],
      darkcolor=[("pressed", pal["border_strong"]), ("active", pal["border_strong"])])

    # 主操作按钮：主色填充 / 白字
    c("Accent.TButton", background=accent, foreground=pal["on_accent"], font=body,
      bordercolor=accent, lightcolor=accent, darkcolor=accent,
      borderwidth=1, relief="flat", padding=(14, 6),
      focusthickness=0, focuscolor=accent)
    m("Accent.TButton",
      background=[("pressed", accent_pressed), ("active", accent_hover),
                  ("disabled", accent_soft)],
      foreground=[("disabled", pal["faint"]), ("active", pal["on_accent"])],
      bordercolor=[("pressed", accent_pressed), ("active", accent_hover),
                   ("disabled", accent_soft)],
      lightcolor=[("pressed", accent_pressed), ("active", accent_hover)],
      darkcolor=[("pressed", accent_pressed), ("active", accent_hover)])

    # 幽灵按钮：默认无边框、低强调，hover 出浅底
    c("Ghost.TButton", background=card, foreground=pal["muted"], font=body,
      bordercolor=card, lightcolor=card, darkcolor=card,
      borderwidth=0, relief="flat", padding=(10, 5), focusthickness=0)
    m("Ghost.TButton",
      background=[("pressed", pal["pressed"]), ("active", pal["hover"])],
      foreground=[("active", pal["text"]), ("pressed", pal["text"])],
      bordercolor=[("pressed", pal["pressed"]), ("active", pal["hover"])],
      lightcolor=[("active", pal["hover"])],
      darkcolor=[("active", pal["hover"])])

    # 破坏性操作
    c("Danger.TButton", background=pal["danger"], foreground="#FFFFFF", font=body,
      bordercolor=pal["danger"], lightcolor=pal["danger"], darkcolor=pal["danger"],
      borderwidth=1, relief="flat", padding=SPACING["btn_pad"], focusthickness=0)
    m("Danger.TButton",
      background=[("pressed", pal["danger_hover"]), ("active", pal["danger_hover"]),
                  ("disabled", pal["danger_soft"])],
      foreground=[("disabled", pal["faint"])],
      bordercolor=[("active", pal["danger_hover"])],
      lightcolor=[("active", pal["danger_hover"])],
      darkcolor=[("active", pal["danger_hover"])])

    # 顶栏工具按钮：更紧凑，底色跟工具栏一致
    c("Tool.TButton", background=pal["toolbar"], foreground=pal["muted"], font=body,
      bordercolor=pal["toolbar"], lightcolor=pal["toolbar"], darkcolor=pal["toolbar"],
      borderwidth=0, relief="flat", padding=(10, 6), focusthickness=0)
    m("Tool.TButton",
      background=[("pressed", pal["pressed"]), ("active", pal["hover"])],
      foreground=[("active", pal["text"]), ("pressed", pal["text"])],
      bordercolor=[("active", pal["hover"])])

    # 分区折叠按钮：卡片上的「▾ 标题」，整行可点，hover 才显色
    c("Section.TButton", background=card, foreground=pal["text"], font=section,
      bordercolor=card, lightcolor=card, darkcolor=card, borderwidth=0,
      relief="flat", padding=(0, 3), anchor="w", focusthickness=0)
    m("Section.TButton",
      background=[("active", pal["card_alt"]), ("pressed", pal["pressed"])],
      foreground=[("active", accent)],
      bordercolor=[("active", pal["card_alt"])],
      lightcolor=[("active", pal["card_alt"])],
      darkcolor=[("active", pal["card_alt"])])

    # ---- 输入类 ----------------------------------------------------------
    c("TEntry", fieldbackground=card, background=card, foreground=pal["text"],
      font=body, bordercolor=pal["border_strong"], lightcolor=pal["border_strong"],
      darkcolor=pal["border_strong"], borderwidth=1, relief="flat",
      padding=SPACING["ctrl_pad"], insertcolor=pal["text"],
      selectbackground=pal["selection"], selectforeground=pal["on_accent"])
    m("TEntry",
      bordercolor=[("focus", accent), ("hover", pal["accent"])],
      fieldbackground=[("disabled", pal["card_alt"]), ("readonly", pal["card_alt"])],
      foreground=[("disabled", pal["faint"])],
      lightcolor=[("focus", accent)],
      darkcolor=[("focus", accent)])

    c("TCombobox", fieldbackground=card, background=card, foreground=pal["text"],
      font=body, arrowcolor=pal["muted"], bordercolor=pal["border_strong"],
      lightcolor=pal["border_strong"], darkcolor=pal["border_strong"],
      borderwidth=1, relief="flat", padding=SPACING["ctrl_pad"],
      insertcolor=pal["text"], selectbackground=pal["selection"],
      selectforeground=pal["on_accent"])
    m("TCombobox",
      fieldbackground=[("readonly", card), ("disabled", pal["card_alt"])],
      foreground=[("readonly", pal["text"]), ("disabled", pal["faint"])],
      arrowcolor=[("active", pal["text"]), ("disabled", pal["faint"])],
      bordercolor=[("focus", accent), ("hover", pal["accent"])],
      background=[("active", pal["hover"])],
      lightcolor=[("focus", accent)],
      darkcolor=[("focus", accent)])

    c("TSpinbox", fieldbackground=card, background=card, foreground=pal["text"],
      font=body, arrowcolor=pal["muted"], bordercolor=pal["border_strong"],
      lightcolor=pal["border_strong"], darkcolor=pal["border_strong"],
      borderwidth=1, relief="flat", padding=SPACING["ctrl_pad"],
      insertcolor=pal["text"], selectbackground=pal["selection"],
      selectforeground=pal["on_accent"])
    m("TSpinbox",
      bordercolor=[("focus", accent), ("hover", pal["accent"])],
      fieldbackground=[("disabled", pal["card_alt"])],
      foreground=[("disabled", pal["faint"])],
      arrowcolor=[("active", pal["text"])],
      lightcolor=[("focus", accent)],
      darkcolor=[("focus", accent)])

    # clam 在根样式 "." 上挂了 selectbackground 的状态 map（[!focus #9e9a91]），
    # 不覆盖的话输入框失去焦点时选中文字会变成土黄色；这里逐控件显式 map 掉。
    for _st in ("TEntry", "TCombobox", "TSpinbox"):
        m(_st,
          selectbackground=[("!disabled", pal["selection"])],
          selectforeground=[("!disabled", pal["on_accent"])])

    # ---- Notebook / 进度条 / 滑杆 ----------------------------------------
    c("TNotebook", background=pal["bg"], borderwidth=0,
      bordercolor=pal["border"], tabmargins=(0, 2, 2, 0))
    c("TNotebook.Tab", background=pal["bg"], foreground=pal["muted"], font=body,
      padding=(14, 7), borderwidth=0, lightcolor=pal["bg"],
      darkcolor=pal["bg"], bordercolor=pal["border"])
    m("TNotebook.Tab",
      background=[("selected", card), ("active", pal["card_alt"])],
      foreground=[("selected", pal["text"]), ("active", pal["text"])],
      lightcolor=[("selected", card), ("active", pal["card_alt"])],
      darkcolor=[("selected", card), ("active", pal["card_alt"])],
      padding=[("selected", (14, 8))])

    c("Horizontal.TProgressbar", background=accent, troughcolor=pal["track"],
      bordercolor=pal["track"], lightcolor=accent, darkcolor=accent, thickness=8)
    c("TProgressbar", background=accent, troughcolor=pal["track"],
      bordercolor=pal["track"], lightcolor=accent, darkcolor=accent, thickness=8)

    # clam 的 Scale：trough 用 troughcolor，滑块本体用 lightcolor/darkcolor，
    # background 会顺带描一圈 1px 外框 —— 所以背景取淡主色（近似隐形），
    # 滑块取主色；gripcount=0 让滑块是实心块而不是竖条纹。
    c("TScale", background=accent_soft, troughcolor=pal["track"],
      bordercolor=pal["border"], lightcolor=accent, darkcolor=accent,
      sliderlength=16, gripcount=0, focuscolor=accent_soft)
    m("TScale",
      lightcolor=[("active", accent_hover), ("pressed", accent_pressed)],
      darkcolor=[("active", accent_hover), ("pressed", accent_pressed)],
      troughcolor=[("active", pal["track"])])
    for _orient in ("Horizontal", "Vertical"):
        c("%s.TScale" % _orient, background=accent_soft, troughcolor=pal["track"],
          bordercolor=pal["border"], lightcolor=accent, darkcolor=accent,
          sliderlength=16, gripcount=0)

    # ---- 勾选 / 单选 -----------------------------------------------------
    c("TCheckbutton", background=pal["bg"], foreground=pal["text"], font=body,
      focuscolor=accent, indicatorbackground=card, indicatorforeground=pal["on_accent"],
      indicatormargin=(0, 2, 6, 2), padding=(0, 4))
    m("TCheckbutton",
      background=[("active", pal["bg"])],
      foreground=[("disabled", pal["faint"])],
      indicatorbackground=[("selected", accent), ("active", card), ("disabled", pal["card_alt"])],
      indicatorforeground=[("selected", pal["on_accent"])])

    c("TRadiobutton", background=pal["bg"], foreground=pal["text"], font=body,
      focuscolor=accent, indicatorbackground=card, indicatorforeground=pal["on_accent"],
      indicatormargin=(0, 2, 6, 2), padding=(0, 4))
    m("TRadiobutton",
      background=[("active", pal["bg"])],
      foreground=[("disabled", pal["faint"])],
      indicatorbackground=[("selected", accent), ("active", card), ("disabled", pal["card_alt"])],
      indicatorforeground=[("selected", pal["on_accent"])])

    # ---- 分隔线 / 滚动条 -------------------------------------------------
    c("TSeparator", background=pal["border"])
    c("TScrollbar", background=pal["border_strong"], troughcolor=pal["bg"],
      bordercolor=pal["bg"], arrowcolor=pal["muted"],
      lightcolor=pal["border_strong"], darkcolor=pal["border_strong"],
      gripcount=0)
    m("TScrollbar",
      background=[("active", pal["muted"]), ("pressed", pal["muted"])],
      arrowcolor=[("active", pal["text"])])

    # ---- 输入框内嵌下拉列表（经典 Listbox，靠 option_add 管，这里兜底）----
    try:
        style.map("TCombobox",
                  fieldbackground=[("readonly", card)],
                  selectbackground=[("readonly", pal["selection"])])
    except Exception:
        pass


def apply_theme(root=None, dark=False):
    """套用主题，返回设计令牌 dict。

    参数
    ----
    root : tk.Tk / tk.Toplevel / None
        None 时只返回 tokens（不碰 ttk，可在无显示环境调用）。
    dark : bool
        默认 False = 浅色。

    返回（键）
    --------
    PALETTE / palette, FONT_FAMILY, FONT_MONO, FONTS, SIZES, SPACING, RADIUS,
    STYLES / styles, TK, SCALING, DARK, THEME, VERSION
    出错时额外带 "ERROR"（字符串），不会抛异常。
    """
    tokens = tokens_for(dark=dark, root=root)
    palette = tokens["PALETTE"]

    if root is None:
        return tokens
    if not TKINTER_OK:
        tokens["ERROR"] = "tkinter 不可用，只返回设计令牌"
        return tokens

    try:
        _apply_tk_defaults(root, palette, tokens["FONTS"],
                           tokens["FONT_FAMILY"], tokens["FONT_MONO"])
    except Exception as exc:  # 经典控件配色失败不影响 ttk
        tokens["ERROR"] = "tk 默认配色失败：%s: %s" % (type(exc).__name__, exc)

    try:
        style = _ttk.Style(root)
        # vista/xpnative 会忽略大半颜色设置，统一切到 clam
        if _THEME_BASE in style.theme_names():
            try:
                if style.theme_use() != _THEME_BASE:
                    style.theme_use(_THEME_BASE)
            except Exception:
                pass
        _configure_styles(style, palette, tokens["FONTS"])
        tokens["THEME"] = style.theme_use()
    except Exception as exc:
        tokens["ERROR"] = "ttk 样式配置失败：%s: %s" % (type(exc).__name__, exc)

    return tokens


# --------------------------------------------------------------------------- 装饰：沙滩条
def _mix_color(color_a, color_b, t):
    """两个 #rrggbb 按 t（0=a，1=b）混合；解析失败返回 a。纯装饰用。"""
    def _rgb(c):
        try:
            c = str(c).lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return None
    a, b = _rgb(color_a), _rgb(color_b)
    if not a or not b:
        return color_a
    t = max(0.0, min(1.0, float(t)))
    return "#%02x%02x%02x" % tuple(
        int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# --------------------------------------------------------------------------- 沙滩纹理
def _luminance(color):
    """#rrggbb 的相对亮度（0-255），用来判断这是不是夜里的沙。"""
    try:
        c = str(color).lstrip("#")
        if len(c) == 3:
            c = "".join(ch * 2 for ch in c)
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        return 0.299 * r + 0.587 * g + 0.114 * b
    except Exception:
        return 255.0


def _sand_texture(w, h, palette, dpi=1.0, seed=7):
    """程序化沙面：低频斑驳 + 风纹 + 沙粒 + 石子 + 湿沙带。

    用 PIL 在 C 层做插值/模糊，比一帧几百个 Canvas 沙粒更自然也更快。
    PIL 不可用时返回 None，BeachStrip 会退回 Canvas 画沙粒。
    """
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageFilter
    except Exception:
        return None
    if w < 24 or h < 20:
        return None
    try:
        rnd = random.Random(seed)
        d = max(0.7, min(2.5, float(dpi or 1.0)))
        sand = palette.get("sidebar", "#F1E4CF")
        foam = palette.get("card", "#FFFCF4")
        warm = palette.get("danger", "#DE6A55")
        night = _luminance(sand) < 96
        light = _mix_color(sand, "#FFFFFF", 0.17 if night else 0.10)
        dark = _mix_color(sand, "#000000", 0.11 if night else 0.075)
        grain_hi_amt = 0.46 if night else 0.28
        grain_lo_amt = 0.34 if night else 0.22
        # 低频：沙丘的深浅斑块
        small = Image.effect_noise(
            (max(2, int(w / (26 * d))), max(2, int(h / (20 * d)))), 80)
        blotch = small.resize((w, h), Image.BICUBIC).filter(
            ImageFilter.GaussianBlur(2.5 * d))
        img = Image.composite(Image.new("RGB", (w, h), light),
                              Image.new("RGB", (w, h), dark), blotch)
        img = Image.blend(Image.new("RGB", (w, h), sand), img,
                          0.58 if night else 0.45)
        # 风纹：缓弧线，压暗一点点
        rip = Image.new("L", (w, h), 0)
        rd = ImageDraw.Draw(rip)
        y = 3 * d
        while y < h:
            amp = rnd.uniform(1.0, 2.8) * d
            per = rnd.uniform(45, 95) * d
            ph = rnd.uniform(0, 6.283)
            pts = []
            x = -12 * d
            while x <= w + 12 * d:
                pts.append((x, y + amp * math.sin(x / per + ph)))
                x += 7 * d
            rd.line(pts, fill=rnd.randint(40, 78),
                    width=max(1, int(rnd.uniform(0.9, 2.1) * d)),
                    joint="curve")
            y += rnd.uniform(7, 14) * d
        rip = rip.filter(ImageFilter.GaussianBlur(0.9 * d))
        img = Image.composite(
            Image.new("RGB", (w, h), _mix_color(sand, "#000000", 0.11)),
            img, rip)
        # 风纹的迎光面：同一条纹往上错 1.5px，压亮一点点
        rip_hi = ImageChops.offset(rip, 0, -max(1, int(1.5 * d)))
        rip_hi = rip_hi.filter(ImageFilter.GaussianBlur(0.7 * d))
        img = Image.composite(
            Image.new("RGB", (w, h), _mix_color(sand, "#FFFFFF", 0.10)),
            img, rip_hi.point(lambda v: int(v * 0.55)))
        # 沙粒：亮/暗两层高频噪点
        n1 = Image.effect_noise((w, h), 30)
        bright = n1.point(lambda v: 255 if v > 224 else 0)
        img = Image.composite(
            Image.new("RGB", (w, h),
                      _mix_color(sand, "#FFFFFF", grain_hi_amt)),
            img, bright.point(lambda v: int(v * (0.62 if night else 0.50))))
        n2 = Image.effect_noise((w, h), 30)
        darkn = n2.point(lambda v: 255 if v < 32 else 0)
        img = Image.composite(
            Image.new("RGB", (w, h),
                      _mix_color(sand, "#000000", grain_lo_amt)),
            img, darkn.point(lambda v: int(v * 0.50)))
        # 沙粒：几千条 1–2px 的短划（亮/暗各一半），这是“沙感”的主要来源
        grain = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grain)
        n = int(max(800, min(9000, w * h / 55.0)))
        for _ in range(n):
            gx = rnd.uniform(0, w)
            gy = rnd.uniform(max(2.0, 5 * d), h)
            ln = rnd.uniform(0.7, 2.4) * d
            a = rnd.randint(26, 72)
            col = ((255, 255, 255, a) if rnd.random() < 0.55
                   else (20, 14, 8, a))
            gd.line([(gx, gy), (gx + ln, gy)], fill=col, width=1)
        for _ in range(max(12, int(n / 90))):
            gx = rnd.uniform(4 * d, w - 4 * d)
            gy = rnd.uniform(h * 0.32, h - 4 * d)
            rr = rnd.uniform(1.2, 2.8) * d
            gd.ellipse((gx - rr, gy - rr * 0.6, gx + rr, gy + rr * 0.6),
                       fill=(255, 250, 240, rnd.randint(30, 70)))
        img = Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")
        # 石子 / 碎壳
        ov = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        od = ImageDraw.Draw(ov)
        for _ in range(max(6, int(w * h / 12000))):
            px = rnd.uniform(5 * d, w - 5 * d)
            py = rnd.uniform(h * 0.28, h - 5 * d)
            pr = rnd.uniform(1.2, 3.0) * d
            col = _mix_color(sand, rnd.choice(["#8E7B63", "#C2B096",
                                               warm, foam]),
                             rnd.uniform(0.25, 0.55))
            od.ellipse((px - pr + 1.5, py - pr * 0.7 + 1.5,
                        px + pr + 1.5, py + pr * 0.7 + 1.5),
                       fill=(0, 0, 0, 30))
            od.ellipse((px - pr, py - pr * 0.72, px + pr, py + pr * 0.72),
                       fill=col)
        img = Image.alpha_composite(img.convert("RGBA"), ov).convert("RGB")
        # 湿沙带：泡沫线以下略微压暗
        wet = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        wd = ImageDraw.Draw(wet)
        fy = int(9 * d)
        for yy in range(max(0, fy), h):
            a = int(4 + 14 * min(1.0, (yy - fy) / max(1.0, (h - fy))))
            wd.line([(0, yy), (w, yy)], fill=(0, 0, 0, a))
        img = Image.alpha_composite(img.convert("RGBA"), wet)
        return img
    except Exception:
        return None


class BeachStrip(_tk.Canvas if _tk is not None else object):
    """工作台底部的「沙滩条」：程序化沙面 + 贝壳 / 海星 / 水草 + 会走的小螃蟹。

    纯视觉组件：不读业务状态，只在自身 Canvas 上画图。沙面优先用 PIL 现生成
    （低频斑驳 + 风纹 + 沙粒 + 石子），没有 PIL 时退回 Canvas 沙粒。浅色/深色都
    靠传进来的 palette 取色，切主题时调 ``set_palette()``。

    动画 110ms 一帧：潮线泡沫、螃蟹的腿和钳子、它留下的脚印、点击涟漪与沙尘。
    ``stop()`` 在窗口关闭时调用。
    """

    FRAME_MS = 110

    def __init__(self, master, palette=None, dpi=1.0, height=168,
                 seed=20261126):
        if _tk is None:  # pragma: no cover - 无 tkinter 环境
            raise RuntimeError("没有 tkinter，无法创建 BeachStrip")
        super().__init__(master, highlightthickness=0, bd=0)
        self._pal = dict(palette or PALETTE)
        self._dpi = max(1.0, float(dpi or 1.0))
        self._logical_h = max(90, int(height))
        self._rnd = random.Random(seed)
        self._phase = 0.0
        self._after = None
        self._ripples = []       # [(x, y, 起始相位)]
        self._marks = []         # [(x, y, 左右脚, 起始相位)]
        self._sand_photo = None
        self._sand_key = None
        self._last_trail = -999
        self._apply_geometry()
        self.bind("<Configure>", self._on_configure)
        self.bind("<Button-1>", self._on_click)
        self._tick()

    # ------------------------------------------------------------ 对外
    def set_palette(self, palette):
        self._pal = dict(palette or self._pal)
        self._apply_geometry()
        self._redraw_sand()
        self._draw_life()

    def stop(self):
        if self._after is not None:
            try:
                self.after_cancel(self._after)
            except Exception:
                pass
            self._after = None

    # ------------------------------------------------------------ 基础
    def _apply_geometry(self):
        try:
            self.configure(bg=self._pal.get("sidebar", "#F1E4CF"),
                           height=int(self._logical_h * self._dpi))
        except Exception:
            pass

    def _colors(self):
        pal = self._pal
        sand = pal.get("sidebar", "#F1E4CF")
        foam = pal.get("card", "#FFFCF4")
        accent = pal.get("accent", "#0C7F8B")
        danger = pal.get("danger", "#DE6A55")
        ink = pal.get("text", "#322C24")
        return {
            "sand": sand, "foam": foam, "accent": accent,
            "danger": danger, "ink": ink,
            "grain_hi": _mix_color(sand, "#FFFFFF", 0.22),
            "grain_lo": _mix_color(sand, "#000000", 0.12),
            "grain_warm": _mix_color(sand, danger, 0.12),
            "outline": _mix_color(ink, sand, 0.45),
            "shadow": _mix_color(sand, "#000000", 0.10),
            "shell": _mix_color(foam, danger, 0.30),
            "shell_ridge": _mix_color(danger, ink, 0.25),
            "star": _mix_color(danger, foam, 0.10),
            "weed": _mix_color("#5E8C5A", sand, 0.30),
            "weed_hi": _mix_color("#7FA96B", sand, 0.15),
        }

    def _on_configure(self, _event=None):
        # 拖动窗口时尺寸连续变化，纹理重建做个轻量去抖
        key = (self.winfo_width(), self.winfo_height())
        if key == getattr(self, "_pending_size", None):
            return
        self._pending_size = key
        try:
            self.after(60, self._redraw_sand)
        except Exception:
            self._redraw_sand()

    # ------------------------------------------------------------ 静态沙滩
    def _redraw_sand(self):
        if _tk is None:
            return
        try:
            if not self.winfo_exists():
                return
            w = max(80, self.winfo_width())
            h = max(60, self.winfo_height())
        except Exception:
            return
        try:
            self.delete("sand")
        except Exception:
            return
        c = self._colors()
        d = self._dpi
        photo_key = (w, h, c["sand"], c["foam"])
        if photo_key != self._sand_key:
            tex = _sand_texture(w, h, self._pal, d)
            self._sand_key = photo_key
            self._sand_photo = None
            if tex is not None:
                try:
                    from PIL import ImageTk
                    self._sand_photo = ImageTk.PhotoImage(tex, master=self)
                except Exception:
                    self._sand_photo = None
        if self._sand_photo is not None:
            # 注意：delete("sand") 会把图片项一起删掉，所以每次重绘都要重新 create_image
            try:
                self.create_image(0, 0, anchor="nw",
                                  image=self._sand_photo, tags="sand")
            except Exception:
                self._sand_photo = None
        if self._sand_photo is None:
            # 没有 PIL：退回 Canvas 沙粒（比纹理朴素，但不会空白）
            n = int(min(1800, max(200, w * h / 60.0)))
            self.create_rectangle(0, 0, w, h, fill=c["sand"], outline="",
                                  tags="sand")
            for _ in range(n):
                x = self._rnd.uniform(0, w)
                y = self._rnd.uniform(3 * d, h)
                r = self._rnd.random()
                col = (c["grain_hi"] if r < 0.45 else
                       c["grain_lo"] if r < 0.82 else c["grain_warm"])
                ln = 1 if self._rnd.random() < 0.72 else 2
                self.create_line(x, y, x + ln, y, fill=col, tags="sand")
        # 贝壳 / 海星 / 水草 / 脚印
        self._draw_shell(w * 0.17, h * 0.64, 14 * d, c)
        self._draw_shell(w * 0.79, h * 0.56, 10 * d, c)
        self._draw_shell(w * 0.90, h * 0.80, 7 * d, c)
        self._draw_star(w * 0.60, h * 0.72, 11 * d, c)
        self._draw_star(w * 0.36, h * 0.90, 7 * d, c)
        for k in range(3):
            self._draw_weed(w * 0.88 + k * 5 * d, h * 0.95, 7 * d, c, k)
        for i in range(4):
            fx = w * 0.33 + i * 17 * d
            fy = h * 0.82 + math.sin(i * 1.7) * 4 * d
            self._draw_foot(fx, fy, i % 2 == 0, d, c, tag="sand", fade=0.0)

    def _draw_shell(self, cx, cy, size, c):
        d = self._dpi
        # 落在沙上的影子
        self.create_oval(cx - size * 0.92, cy + size * 0.10,
                         cx + size * 0.92, cy + size * 0.52,
                         fill=c["shadow"], outline="", tags="sand")
        # 扇形壳面
        pts = [cx, cy]
        for i in range(9):
            a = math.pi * (1.03 + 0.94 * i / 8.0)
            rr = size * (1.0 - 0.04 * math.sin(i * 2.1))
            pts += [cx + rr * math.cos(a), cy + size * 0.80 * math.sin(a)]
        self.create_polygon(pts, fill=c["shell"], outline=c["outline"],
                            width=max(1, int(1.1 * d)), smooth=True,
                            tags="sand")
        # 放射肋
        for i in range(7):
            a = math.pi * (1.10 + 0.80 * i / 6.0)
            self.create_line(
                cx, cy,
                cx + size * 0.92 * math.cos(a),
                cy + size * 0.74 * math.sin(a),
                fill=c["shell_ridge"], width=1, tags="sand")
        # 壳口 + 高光
        self.create_oval(cx - 2.2 * d, cy - 1.8 * d, cx + 2.2 * d, cy + 1.4 * d,
                         fill=c["foam"], outline=c["outline"], width=1,
                         tags="sand")
        self.create_arc(cx - size * 0.55, cy - size * 0.62,
                        cx + size * 0.55, cy - size * 0.02,
                        start=25, extent=130, style="arc",
                        outline=c["foam"], width=1, tags="sand")

    def _draw_star(self, cx, cy, r, c):
        d = self._dpi
        self.create_oval(cx - r * 0.9, cy + r * 0.25,
                         cx + r * 0.9, cy + r * 0.62,
                         fill=c["shadow"], outline="", tags="sand")
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5
            rr = r if i % 2 == 0 else r * 0.40
            pts += [cx + rr * math.cos(ang), cy + rr * math.sin(ang)]
        self.create_polygon(pts, fill=c["star"], outline=c["outline"],
                            width=max(1, int(1.1 * d)), smooth=True,
                            tags="sand")
        # 五条腕上的小疙瘩
        for i in range(5):
            ang = -math.pi / 2 + i * 2 * math.pi / 5
            for t in (0.35, 0.6, 0.82):
                self.create_oval(
                    cx + r * t * math.cos(ang) - 1.1 * d,
                    cy + r * t * math.sin(ang) - 1.1 * d,
                    cx + r * t * math.cos(ang) + 1.1 * d,
                    cy + r * t * math.sin(ang) + 1.1 * d,
                    fill=c["shell_ridge"], outline="", tags="sand")
        self.create_oval(cx - 2.0 * d, cy - 2.0 * d,
                         cx + 2.0 * d, cy + 2.0 * d,
                         fill=c["shell_ridge"], outline="", tags="sand")

    def _draw_weed(self, x0, y0, step, c, k):
        for j, (col, wdt, dx) in enumerate(((c["weed"], 1.6, 0),
                                            (c["weed_hi"], 0.9, 1.6))):
            pts = [x0 + dx * self._dpi, y0]
            for j2 in range(1, 7):
                pts += [x0 + dx * self._dpi
                        + (3.4 + j) * self._dpi * math.sin(j2 * 1.15 + k),
                        y0 - j2 * step]
            self.create_line(pts, fill=col, smooth=True,
                             width=max(1, int(wdt * self._dpi)), tags="sand")

    def _draw_foot(self, x, y, left, d, c, tag="mark", fade=0.0):
        """一个压暗的脚掌印：窄长的脚掌 + 前面三颗脚趾，左右脚错开。"""
        k = max(0.0, min(1.0, 1.0 - fade))
        if k <= 0.02:
            return
        col = _mix_color(c["sand"], "#000000", 0.18 * k)
        off = (-2.4 if left else 2.4) * d
        pts = [
            (x + off - 1.9 * d, y - 2.0 * d),
            (x + off + 1.9 * d, y - 2.0 * d),
            (x + off + 1.7 * d, y + 1.2 * d),
            (x + off + 1.0 * d, y + 4.2 * d),
            (x + off, y + 5.8 * d),
            (x + off - 1.0 * d, y + 4.2 * d),
            (x + off - 1.7 * d, y + 1.2 * d),
        ]
        self.create_polygon(pts, fill=col, outline="", smooth=True, tags=tag)
        for dx, dy in ((-1.6, 0.5), (0.0, 0.0), (1.5, 0.45)):
            tx = x + off + dx * d
            ty = y - 3.5 * d + dy * d
            self.create_oval(tx - 0.85 * d, ty - 1.1 * d,
                             tx + 0.85 * d, ty + 1.1 * d,
                             fill=col, outline="", tags=tag)

    # ------------------------------------------------------------ 活动层
    def _draw_life(self):
        if _tk is None:
            return
        try:
            if not self.winfo_exists():
                return
            w = max(80, self.winfo_width())
            h = max(60, self.winfo_height())
        except Exception:
            return
        try:
            self.delete("life")
            self.delete("foam")
            self.delete("glow")
            self.delete("ripple")
            self.delete("mark")
        except Exception:
            return
        c = self._colors()
        d = self._dpi
        t = self._phase
        self._draw_foam(w, h, d, c, t)
        self._draw_night_glow(w, h, d, c, t)
        # 小螃蟹：左右横着走，身后留脚印
        pad = 34 * d
        span = max(10.0, w - 2 * pad)
        cx = pad + span * (0.5 + 0.5 * math.sin(t * 0.045))
        cy = h - 30 * d + 1.5 * d * math.sin(t * 0.42)
        moving = abs(math.cos(t * 0.045)) > 0.25
        self._draw_crab(cx, cy, d, t, c, moving)
        if moving and t - self._last_trail > 16:
            self._last_trail = t
            side = (int(t) // 16) % 2 == 0
            self._marks.append((cx - 14 * d, cy + 9 * d, side, t))
            if len(self._marks) > 22:
                self._marks = self._marks[-22:]

        # 点击涟漪：三圈 + 中心水花 + 沙尘
        alive = []
        for (rx, ry, t0) in self._ripples:
            age = max(0.0, t - t0)
            if age > 16:
                continue
            alive.append((rx, ry, t0))
            k = age / 16.0
            for j in range(3):
                rr = (7 + 40 * k) * d * (1 - 0.20 * j)
                col = _mix_color(c["foam"], c["sand"],
                                 min(1.0, 0.18 + k * 0.9))
                self.create_oval(rx - rr, ry - rr * 0.42,
                                 rx + rr, ry + rr * 0.42,
                                 outline=col, width=max(1, int(1.3 * d)),
                                 tags="ripple")
            if age < 3:
                self.create_oval(rx - 3 * d, ry - 1.4 * d,
                                 rx + 3 * d, ry + 1.4 * d,
                                 fill=c["foam"], outline="", tags="ripple")
            if age < 7:
                for s in range(6):
                    ang = s * math.pi / 3 + 0.3
                    rr = (4 + age * 3.2) * d
                    self.create_oval(rx + rr * math.cos(ang) - 0.9 * d,
                                     ry + rr * math.sin(ang) * 0.5 - 0.9 * d,
                                     rx + rr * math.cos(ang) + 0.9 * d,
                                     ry + rr * math.sin(ang) * 0.5 + 0.9 * d,
                                     fill=_mix_color(c["sand"], c["foam"], 0.5),
                                     outline="", tags="ripple")
        self._ripples = alive

        # 脚印（点击 + 螃蟹走过），约 13 秒淡完
        keep = []
        for (mx, my, left, t0) in self._marks:
            age = max(0.0, t - t0)
            if age > 120:
                continue
            keep.append((mx, my, left, t0))
            self._draw_foot(mx, my, left, d, c, tag="mark",
                            fade=age / 120.0)
        self._marks = keep

    def _draw_foam(self, w, h, d, c, t):
        """会动的潮线：泡沫弧线 + 漂浮气泡。"""
        base_y = 9 * d
        lift = 1.6 * d * math.sin(t * 0.045)
        pts = []
        x = -10 * d
        while x <= w + 10 * d:
            pts += [x, base_y + lift
                    + 2.3 * d * math.sin(x / (44 * d) + t * 0.03)]
            x += 7 * d
        self.create_line(pts, fill=_mix_color(c["foam"], c["sand"], 0.18),
                         width=max(1, int(2.2 * d)), smooth=True, tags="foam")
        pts2 = [v + (0 if i % 2 == 0 else 3.2 * d)
                for i, v in enumerate(pts)]
        self.create_line(pts2, fill=c["accent"], width=1, smooth=True,
                         tags="foam")
        for i in range(11):
            bx = ((i * 0.093 + 0.04) * w + t * 0.5 * d) % max(1, w)
            by = base_y + lift + 2.5 * d * math.sin(bx / (31 * d) + i)
            rr = (0.7 + 0.5 * abs(math.sin(t * 0.06 + i))) * d
            self.create_oval(bx - rr, by - rr, bx + rr, by + rr,
                             outline=c["foam"], width=1, tags="foam")

    def _draw_night_glow(self, w, h, d, c, t):
        """夜里的生物荧光：潮线附近一颗颗会呼吸的青绿光点。"""
        if _luminance(c["sand"]) >= 96:
            return
        for i in range(10):
            gx = (0.05 + i * 0.10) * w + 5 * d * math.sin(t * 0.04 + i * 1.3)
            gy = 16 * d + 7 * d * math.sin(t * 0.055 + i * 1.9)
            pulse = 0.30 + 0.70 * abs(math.sin(t * 0.07 + i * 1.1))
            core = _mix_color(c["accent"], "#E8FFFB", pulse)
            r = (1.0 + 0.9 * pulse) * d
            self.create_oval(gx - r * 3.4, gy - r * 2.1,
                             gx + r * 3.4, gy + r * 2.1,
                             fill=_mix_color(c["sand"], core, 0.16),
                             outline="", tags="glow")
            self.create_oval(gx - r, gy - r, gx + r, gy + r,
                             fill=core, outline="", tags="glow")

    def _draw_crab(self, x, y, d, t, c, moving=True):
        body = _mix_color(c["danger"], c["ink"], 0.18)
        dark = _mix_color(c["ink"], c["danger"], 0.30)
        light = _mix_color(c["foam"], body, 0.35)
        # 影子
        self.create_oval(x - 15 * d, y + 5 * d, x + 15 * d, y + 9 * d,
                         fill=c["shadow"], outline="", tags="life")
        # 八条腿（两节），交替抬起
        for i in range(4):
            ox = (-1.5 + i) * 4.6 * d
            for s in (-1, 1):
                if moving:
                    lift = max(0.0, math.sin(t * 0.7 + i * 1.5
                                             + (0 if s < 0 else 3.14))) * 2.2 * d
                else:
                    lift = 0.0
                x1 = x + ox + s * 6.0 * d
                y1 = y + 2.0 * d
                x2 = x + ox + s * (10.0 + i * 1.5) * d
                y2 = y + 7.4 * d - lift
                x3 = x + ox + s * (11.6 + i * 1.7) * d
                y3 = y + 10.2 * d - lift * 0.55
                self.create_line([(x1, y1), (x2, y2), (x3, y3)],
                                 fill=dark, width=max(1, int(1.5 * d)),
                                 smooth=True, tags="life")
        # 壳：扁一点，带两道甲壳纹
        self.create_oval(x - 12 * d, y - 5.2 * d, x + 12 * d, y + 6.4 * d,
                         fill=body, outline=dark, width=1, tags="life")
        self.create_arc(x - 9 * d, y - 4.4 * d, x + 9 * d, y + 2.6 * d,
                        start=16, extent=148, style="arc",
                        outline=light, width=1, tags="life")
        self.create_arc(x - 5.5 * d, y - 3.2 * d, x + 5.5 * d, y + 3.2 * d,
                        start=20, extent=140, style="arc",
                        outline=light, width=1, tags="life")
        # 两只钳子：一开一合，钳口有缺口
        open_ = 1.8 * d * math.sin(t * 0.5)
        for s in (-1, 1):
            bx = x + s * 12 * d
            by = y - 2.4 * d
            self.create_line(bx, by, bx + s * 5 * d, by - 4.6 * d,
                             fill=dark, width=max(1, int(1.5 * d)),
                             smooth=True, tags="life")
            hx = bx + s * 5 * d
            hy = by - 4.6 * d
            self.create_oval(hx - 3.4 * d, hy - 2.6 * d + open_,
                             hx + 3.4 * d, hy + 1.2 * d + open_,
                             fill=body, outline=dark, width=1, tags="life")
            self.create_line(hx, hy + 0.4 * d,
                             hx + s * 3.2 * d, hy + 1.0 * d + open_,
                             fill=light, width=1, tags="life")
        # 眼睛（带眼柄）+ 两根会晃的触角
        for s in (-1, 1):
            ex = x + s * 3.8 * d
            ey = y - 8.6 * d
            self.create_line(ex, y - 5.2 * d, ex, ey, fill=dark, width=1,
                             tags="life")
            self.create_oval(ex - 1.25 * d, ey - 1.25 * d,
                             ex + 1.25 * d, ey + 1.25 * d,
                             fill=c["ink"], outline="", tags="life")
            self.create_line(x + s * 2.8 * d, y - 6.4 * d,
                             x + s * 6.5 * d,
                             y - 11 * d + 1.2 * d * math.sin(t * 0.9 + s),
                             fill=dark, width=1, tags="life")

    # ------------------------------------------------------------ 动画 / 交互
    def _on_click(self, event):
        try:
            self._ripples.append((float(event.x), float(event.y), self._phase))
            if len(self._ripples) > 6:
                self._ripples = self._ripples[-6:]
            left = len(self._marks) % 2 == 0
            self._marks.append((float(event.x), float(event.y), left,
                                self._phase))
            if len(self._marks) > 22:
                self._marks = self._marks[-22:]
        except Exception:
            pass

    def _tick(self):
        self._after = None
        try:
            if not self.winfo_exists():
                return
            self._phase += 1
            if self.winfo_viewable():
                self._draw_life()
        except Exception:
            return
        try:
            self._after = self.after(self.FRAME_MS, self._tick)
        except Exception:
            self._after = None



# --------------------------------------------------------------------------- 可复用海面
def paint_sea(canvas, cw, ch, palette=None, phase=0.0, dpi=1.0, tag="seabg"):
    """在任意 Canvas 上画一层会动的海面：渐变 + 浪 + 碎金 + 海鸥 + 鱼。

    gui.py 有自己的完整版（夜海/月亮/波纹）；做题窗口这类只需要「有海」的窗口
    直接调用这个函数即可，画完自动 tag_lower 到最底层。
    """
    if canvas is None or cw < 24 or ch < 24:
        return
    try:
        pal = palette or PALETTE
        base = pal.get("canvas", "#D5E7E6")
        accent = pal.get("accent", "#0C7F8B")
        on = pal.get("on_accent", "#FFFFFF")
        foam = _mix_color(base, "#FFFFFF", 0.90)
        ink = _mix_color(base, accent, 0.62)
        glint = _mix_color(base, (pal or {}).get("warning", "#E8A33D"),
                           0.85)
        d = max(1.0, float(dpi or 1.0))
        t = float(phase)
        canvas.delete(tag)
        deep = _mix_color(base, accent, 0.20)
        steps = 14
        for i in range(steps):
            k = i / float(max(1, steps - 1))
            y = ch * i / float(steps)
            canvas.create_line(0, y, cw, y, fill=_mix_color(base, deep, k),
                               width=max(2, int(ch / steps) + 2), tags=tag)
        layers = ((ch * 0.26, 2.0 * d, 0.16, 0.030, False),
                  (ch * 0.46, 2.4 * d, 0.19, 0.045, False),
                  (ch * 0.64, 2.8 * d, 0.23, 0.060, True),
                  (ch * 0.80, 2.6 * d, 0.28, 0.080, True),
                  (ch - 9 * d, 1.8 * d, 0.20, 0.100, False))
        for idx, (yy, amp, amt, spd, caps) in enumerate(layers):
            pts = []
            x = -20.0
            while x <= cw + 20:
                pts += [x, yy + amp * math.sin(x / (110 * d) + t * spd + idx)]
                x += 15 * d
            canvas.create_line(pts, fill=_mix_color(base, accent, amt),
                               width=max(1, int(1.6 * d)), smooth=True,
                               tags=tag)
            if caps:
                for k in range(5):
                    fx = ((k * 0.2 + 0.06) * cw
                          + 20 * d * math.sin(t * 0.02 + k))
                    fx = max(20 * d, min(cw - 20 * d, fx))
                    y0 = yy + amp * math.sin(
                        (fx - 7 * d) / (110 * d) + t * spd + idx)
                    y1 = yy + amp * math.sin(fx / (110 * d) + t * spd + idx)
                    y2 = yy + amp * math.sin(
                        (fx + 7 * d) / (110 * d) + t * spd + idx)
                    canvas.create_line([(fx - 8 * d, y0), (fx, y1),
                                        (fx + 8 * d, y2)],
                                       fill=foam, width=max(1, int(1.8 * d)),
                                       smooth=True, tags=tag)
        for i in range(24):
            sx = 14 + ((i * 177.3 + 41) % max(1.0, cw - 28))
            sy = 14 + ((i * 91.7 + 53) % max(1.0, ch - 28))
            tw = math.sin(t * 0.14 + i * 1.8)
            if tw < 0.4:
                continue
            r = (1.0 + 1.9 * tw) * d
            col = _mix_color(glint, "#FFFFFF", 0.35 + 0.5 * tw)
            canvas.create_line(sx - r, sy, sx + r, sy, fill=col, width=1,
                               tags=tag)
            canvas.create_line(sx, sy - r, sx, sy + r, fill=col, width=1,
                               tags=tag)
        for k, (gy, spd, sc) in enumerate(((26 * d, 1.0, 1.0),
                                           (40 * d, 0.75, 0.62))):
            gx = ((t * spd * d + k * 300) % (cw + 140 * d)) - 70 * d
            flap = (2.4 + 2.2 * math.sin(t * 0.36 + k * 1.6)) * sc * d
            by = gy + 3 * d * math.sin(t * 0.07 + k)
            canvas.create_line(
                [(gx - 9 * sc * d, by + flap), (gx, by - 1.2 * d),
                 (gx + 9 * sc * d, by + flap)],
                fill=ink, width=max(1, int(1.5 * d)), smooth=True,
                tags=tag)
        for k, (yy, spd, sgn) in enumerate(((ch * 0.28, 0.9, 1),
                                            (ch * 0.80, -0.7, -1))):
            fx = ((t * spd * 1.3 * d + k * 240) % (cw + 180 * d)) - 90 * d
            fy = yy + 3 * d * math.sin(t * 0.11 + k)
            wag = 2.2 * d * math.sin(t * 0.5 + k * 2)
            canvas.create_oval(fx - 12 * d, fy - 4.8 * d,
                               fx + 12 * d, fy + 4.8 * d,
                               outline=ink, width=max(1, int(1.6 * d)),
                               tags=tag)
            canvas.create_polygon(fx - sgn * 11 * d, fy,
                                  fx - sgn * 18 * d, fy - 4.8 * d + wag,
                                  fx - sgn * 18 * d, fy + 4.8 * d + wag,
                                  fill="", outline=ink,
                                  width=max(1, int(1.6 * d)), tags=tag)
            canvas.create_oval(fx + sgn * 6.6 * d - 1.2 * d, fy - 1.2 * d,
                               fx + sgn * 6.6 * d + 1.2 * d, fy + 1.2 * d,
                               fill=ink, outline="", tags=tag)
            for b in range(3):
                bx = fx - sgn * (20 + b * 6) * d
                by = fy - (b + 1) * 4 * d + 2 * d * math.sin(
                    t * 0.4 + b + k)
                rr = (0.8 + 0.5 * b) * d
                canvas.create_oval(bx - rr, by - rr, bx + rr, by + rr,
                                   outline=ink, width=1, tags=tag)
        # 气泡：慢慢浮起来的小圈
        for i in range(7):
            bx = 30 + ((i * 149.7 + 40) % max(1.0, cw - 60))
            by = ch - ((t * 2.0 * d + i * 57) % max(1.0, ch * 0.7))
            br = (2.2 + 1.4 * (i % 3)) * d
            canvas.create_oval(bx - br, by - br, bx + br, by + br,
                               outline=foam, width=1, tags=tag)
        canvas.tag_lower(tag)
    except Exception:
        pass

if __name__ == "__main__":  # 手动自测：py -3 theme.py
    import json
    import sys

    use_tk = "--nogui" not in sys.argv
    result = apply_theme(None, dark="--dark" in sys.argv)
    print("apply_theme(None) OK ->", sorted(result.keys()))
    print("PALETTE keys:", len(result["PALETTE"]), "| STYLES:", len(result["STYLES"]))
    print("字体:", result["FONT_FAMILY"], "|", result["FONT_MONO"])
    print("available():", available())
    if use_tk and available():
        try:
            r = _tk.Tk()
            r.withdraw()
            token2 = apply_theme(r, dark="--dark" in sys.argv)
            print("apply_theme(root) OK -> theme =", token2.get("THEME"),
                  "| ERROR =", token2.get("ERROR"))
            print("SCALING:", round(token2["SCALING"], 3))
            r.destroy()
        except Exception as exc:  # pragma: no cover
            print("Tk 实测失败:", type(exc).__name__, exc)
    print(json.dumps({"h1": list(result["FONTS"]["h1"]),
                      "section": list(result["FONTS"]["section"]),
                      "body": list(result["FONTS"]["body"]),
                      "muted": list(result["FONTS"]["muted"])},
                     ensure_ascii=False))
