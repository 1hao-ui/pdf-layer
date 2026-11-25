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

import os

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

# 深色：夜海 —— 深海蓝底 + 月光青主色；纸面依旧是暖白
PALETTE_DARK = {
    "bg": "#101F27",
    "card": "#182A33",
    "card_alt": "#14242C",
    "sidebar": "#0D1A21",
    "toolbar": "#142530",
    "panel": "#142530",
    "log": "#142530",
    "canvas": "#09141A",
    "canvas_dot": "#22343B",
    "track": "#273A43",
    "border": "#2A3E47",
    "border_strong": "#3A515B",
    "text": "#EAF2F0",
    "muted": "#9DAFB2",
    "faint": "#6D8286",
    "on_accent": "#FFFFFF",
    "accent": "#45BEC8",        # 月光青
    "accent_hover": "#63D2DB",
    "accent_pressed": "#2FA6B1",
    "accent_soft": "#163A41",
    "success": "#4FD3A0",
    "success_soft": "#15322A",
    "danger": "#F08A78",
    "danger_hover": "#E0695A",
    "danger_soft": "#3A2621",
    "hover": "#1C303A",
    "pressed": "#182A33",
    "selection": "#1D4E57",
    "paper": "#FFFDF8",         # 纸永远是暖白的（深色界面里更亮眼）
    "paper_edge": "#2A3E47",
    "paper_ink": "#27231C",     # 纸上的字永远用深墨色
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
