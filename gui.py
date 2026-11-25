# -*- coding: utf-8 -*-
"""gui.py — 手写体生成器 GUI（多段落 / 背景导入 / PDF 内书写 / 主题化）。

与共享核心的分工
----------------
· ``page_model.py``：页面 / 段落模型、背景（空白 A4 / 图片 / PDF 某页）、
  整页渲染、导出 PNG / PDF（有源 PDF 时保留原内容只叠手写层）、项目 spec。
· ``symbols.py``：``make_han_hand()``（★ 我的手写体，默认）、``make_hand()``。
· ``hwrender.py``：渲染引擎（``Hand``、``SCALE``、默认字号 / 行距 / 墨色）。
· ``theme.py``：可选。``apply_theme(root, dark=False) -> dict`` 返回调色板并配置
  ttk 样式；theme 还没就绪时本模块用内置浅色 / 深色调色板 + 自建命名样式兜底。
· ``ai_api.py``：可选。工具栏「AI 接口」用 ``py -3 ai_api.py`` 子进程启动，
  这里只负责拉起进程 + 探测端口 + 显示状态，不 import 它。

界面结构（2026-11 沙滩主题）
---------------------------
三个页面共用同一块画布，只切换左侧工作台；次要动作全部收进「⋯」菜单。

┌────────────────────────────────────────────────────────────────────┐
│ ≈ 手写体生成器   [书写][样式][输出]                    渲染  ⋯      │
│ ≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋ 海浪 ≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋≋ │
├───────────────┬────────────────────────────────────────────────────┤
│ 书写：段落     │  整页画布（浅海）：暖白纸 + 多段落手写层            │
│   列表 / 正文  │  矩形可拖动 / 缩放；纸面空白处拖框 = 重设当前段区域  │
│   + 背景按钮   │  滚轮以光标为中心缩放，中键 / 空格拖动，双击适应    │
│ 样式：字体 /   │                                                    │
│   墨色 / 高级  │                                                    │
│ 输出：导出 /   │                                                    │
│   项目 / AI    │                                                    │
├───────────────┴────────────────────────────────────────────────────┤
│ 日志抽屉（默认收起；「⋯」菜单或输出页打开，保留旧命令）            │
├────────────────────────────────────────────────────────────────────┤
│ 状态栏：背景 / 页尺寸 / 段数 / 缩放（缺字时才追加覆盖率提示）        │
└────────────────────────────────────────────────────────────────────┘

配色与控件样式全部来自 ``theme.py``（沙滩 / 夜海两套调色板）；本文件不写死颜色，
只通过 ``self.pal`` / ttk 语义样式名取色。

线程规则
--------
tkinter 控件只在主线程碰：渲染时先 ``page_to_spec()`` 快照 + 复制渲染参数，
子线程用 ``page_model`` 重新构造页面并渲染，结果通过 ``root.after`` 回主线程。
``App.render_sync()`` 是给测试 / 导出用的同步入口（阻塞主线程，别在 UI 事件里用）。

保留的旧接口（_work/verify_han_integration.py 等依赖）
------------------------------------------------------
``DEFAULT_FONT`` / ``DEFAULT_FONT_LABEL`` / ``FONT_LABELS`` / ``MATH_FONT_DEFAULT`` /
``_font_path()`` / ``_variant_fonts()`` / ``SAMPLE_TEXT`` / ``App(root)`` /
控制台命令 help / render / save png / save pdf / size= / line= / jitter= / seed= /
width= / ink= / font= / variants= / page= / Ctrl+Enter 渲染 / feedback.md 记录。
"""
from __future__ import annotations

import json
import math
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hwrender as H          # noqa: E402
import symbols                # noqa: E402
import page_model as PM       # noqa: E402
from PIL import Image         # noqa: E402

try:                          # tkinter 缺失时也允许 `import gui`（测试/无显示环境）
    import tkinter as _tk
    from tkinter import ttk as _ttk
except Exception:             # pragma: no cover - 正常 Windows 环境不会走到
    _tk = None
    _ttk = None

FEEDBACK = os.path.join(HERE, "feedback.md")
OUT_DIR = os.path.join(HERE, "out")
CONFIG_PATH = os.path.join(OUT_DIR, "gui_config.json")
PROJECT_DIR = os.path.normpath(os.path.join(HERE, "..", ".."))

AI_DEFAULT_PORT = int(os.environ.get("AI_API_PORT") or 8765)

# ---------------------------------------------------------------- 字体
HAN_OWN = "__HAN_OWN__"      # 「我的手写体」别名：当前启用的汉字库 + 符号库


def _legacy_font_labels():
    """老的 ZTi 亲笔字库（作者本人笔迹，留作对比/回退，不是第三方字体）。"""
    d = os.path.join(HERE, "fonts", "ZTi")
    if not os.path.isdir(d):
        return []
    return [("ZTi 我的手写·字嗨 4 套", "ZTi/ZTi-01.otf"),
            ("ZTi 我的手写·真迹扫描 4 套", "ZTi/ZTi-Scan-01.ttf")]


def font_labels():
    """字体下拉框：我的手写体 + 注册表里的自建字库 + 老 ZTi 亲笔库。

    不再列任何第三方字体（2026-11-25 已全部移出字库）。新建的字库会在
    「字库工作台」里自动出现在这里。
    """
    labels = [("★ 我的手写体（汉字+符号，全手写）", HAN_OWN)]
    try:
        from fontlib import registry
        for lib in registry.libraries(kind="han", enabled_only=True):
            if lib.get("builtin"):
                continue        # 内置库就是「★ 我的手写体」，不重复列
            labels.append(("我的字库 · %s（%d 字）" % (
                lib.get("name") or lib["id"], lib.get("char_count") or 0),
                "lib:" + lib["id"]))
    except Exception:
        pass
    labels += _legacy_font_labels()
    return labels


FONT_LABELS = font_labels()
MATH_FONT_DEFAULT = "ZTi-Sym"      # 逻辑名，_font_path() 解析成当前符号库主字体

# 默认字体：始终用「我的手写体」（注册表没配好时由 _font_path 兜底）
DEFAULT_FONT = HAN_OWN


def _label_for(fn):
    """字体文件名 -> 下拉框里的显示名。"""
    for lab, f in FONT_LABELS:
        if f == fn:
            return lab
    if fn and str(fn).startswith("lib:"):
        return "我的字库"
    return FONT_LABELS[0][0]


DEFAULT_FONT_LABEL = _label_for(DEFAULT_FONT)


def _font_path(name):
    """字体文件名 -> 路径；别名解析成注册表里当前启用的字库。"""
    if name == HAN_OWN:
        p = symbols.han_main_font()
        if p:
            return p
        name = "ZTi/ZTi-Scan-01.ttf"     # 字库缺失时兜底，不崩
    if name == MATH_FONT_DEFAULT:
        p = symbols.sym_main_font()
        if p:
            return p
        name = "ZTi/ZTi-Scan-01.ttf"
    if isinstance(name, str) and name.startswith("lib:"):
        try:
            from fontlib import registry
            p = registry.main_font(name[4:])
            if p:
                return p
        except Exception:
            pass
        return symbols.han_main_font() or ""
    return os.path.join(HERE, "fonts", name)


SAMPLE_TEXT = r"""4.3.5 解：图4.03中，三角形联结的$Z_b$与星形联结的$Z_a$并联接入线电压$380\,\mathrm{V}$的三相电源。
星形部分：相电压$U_{\mathrm{相}}=\frac{380}{\sqrt{3}}\approx 220\,\mathrm{V}$，$|Z_a|=\sqrt{3^2+4^2}=5\,\Omega$，相电流$I=\frac{220}{5}=44\,\mathrm{A}$。
有功$P_a=3\times 44^2\times 3\approx 17.4\,\mathrm{kW}$，无功$Q_a=3\times 44^2\times 4\approx 23.2\,\mathrm{kvar}$（感性）。
总功率$P=\sum_{i=1}^{n}P_i\approx 52.1\,\mathrm{kW}$，$S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，$\cos\varphi\approx 0.999$。"""


def _variant_fonts(main_name=None):
    """同字多形的其它套（自建字库的 pass2..passN，或老的 ZTi 8 套）。

    · 主字体来自注册表（lib:xxx）：把该库除主字体外的各遍全给。
    · 主字体是老的 ZTi：只轮换**同组**另外 3 套——字嗨（ZTi-0N.otf）
      与真迹扫描（ZTi-Scan-0N.ttf）墨迹占比差 1.4 倍，护栏会拒绝混编；
      分成两组各转各的，同字多形才真正生效。
    """
    if isinstance(main_name, str) and main_name.startswith("lib:"):
        try:
            from fontlib import registry
            paths = registry.pass_paths(main_name[4:])
            return [p for p in paths[1:]]
        except Exception:
            return []
    d = os.path.join(HERE, "fonts", "ZTi")
    if not os.path.isdir(d):
        return []
    files = [n for n in sorted(os.listdir(d)) if n.endswith((".otf", ".ttf"))]
    if main_name and main_name.replace("\\", "/").startswith("ZTi/"):
        own = os.path.basename(main_name.replace("\\", "/"))
        want_scan = "Scan" in own
        group = [n for n in files if ("Scan" in n) == want_scan]
        return [os.path.join(d, n) for n in group if n != own]
    return [os.path.join(d, n) for n in files]


# ================================================================ DPI
_DPI_DONE = False


def enable_dpi_awareness():
    """Windows DPI 感知：越早调用越好（Tk 创建前）。失败无害，返回是否成功。"""
    global _DPI_DONE
    if _DPI_DONE:
        return True
    _DPI_DONE = True
    if os.name != "nt":
        return False
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)   # PROCESS_SYSTEM_DPI_AWARE
            return True
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
                return True
            except Exception:
                return False
    except Exception:
        return False


# import 时就设，保证真正 Tk() 之前已经生效（main() 里还会再调一次）
enable_dpi_awareness()


# ================================================================ 调色板 / 主题
FALLBACK_LIGHT = {
    "bg": "#F6EDDD",
    "panel": "#FFFCF4",
    "sidebar": "#F1E4CF",
    "card": "#FFFCF4",
    "card_alt": "#F8EFDE",
    "text": "#322C24",
    "muted": "#7B7061",
    "border": "#E7D9C2",
    "accent": "#0C7F8B",
    "accent_hover": "#0A6C77",
    "accent_text": "#FFFFFF",
    "danger": "#DE6A55",
    "danger_hover": "#C8553F",
    "danger_soft": "#FAE6DE",
    "canvas": "#D5E7E6",
    "canvas_dot": "#BFD6D3",
    "log_bg": "#FFFCF4",
    "log_fg": "#322C24",
    "selection": "#DBEFEE",
    "selection_text": "#0C7F8B",
    "white": "#FFFDF8",
    "shadow": "#6A5B45",
}

FALLBACK_DARK = {
    "bg": "#101F27",
    "panel": "#142530",
    "sidebar": "#0D1A21",
    "card": "#182A33",
    "card_alt": "#14242C",
    "text": "#EAF2F0",
    "muted": "#9DAFB2",
    "border": "#2A3E47",
    "accent": "#45BEC8",
    "accent_hover": "#63D2DB",
    "accent_text": "#FFFFFF",
    "danger": "#F08A78",
    "danger_hover": "#E0695A",
    "danger_soft": "#3A2621",
    "canvas": "#09141A",
    "canvas_dot": "#22343B",
    "log_bg": "#142530",
    "log_fg": "#EAF2F0",
    "selection": "#1D4E57",
    "selection_text": "#FFFFFF",
    "white": "#FFFDF8",
    "shadow": "#000000",
}

# 主题模块可能用别的键名；这里做一层别名映射，统一到上面的规范键。
_PAL_ALIASES = {
    "bg": ("bg", "background", "root_bg", "window_bg", "app_bg", "bg_color"),
    "panel": ("panel", "surface", "panel_bg", "toolbar_bg", "topbar_bg"),
    "sidebar": ("sidebar", "sidebar_bg", "side_bg", "panel_alt"),
    "card": ("card", "card_bg", "surface_alt", "frame_bg"),
    "card_alt": ("card_alt", "field_bg", "input_bg", "entry_bg", "alt_bg"),
    "text": ("text", "fg", "foreground", "text_color", "ink_text", "on_bg"),
    "muted": ("muted", "muted_text", "secondary_text", "subtle", "dim"),
    "border": ("border", "line", "divider", "border_color", "outline"),
    "accent": ("accent", "primary", "accent_color", "brand", "highlight"),
    "accent_hover": ("accent_hover", "accent_active", "primary_hover"),
    "accent_text": ("accent_text", "on_accent", "accent_fg", "accent_foreground"),
    "danger": ("danger", "error", "danger_color", "red", "warn"),
    "danger_hover": ("danger_hover", "error_hover", "red_hover"),
    "danger_soft": ("danger_soft", "danger_bg", "error_soft", "red_soft"),
    "canvas": ("canvas", "canvas_bg", "workspace", "preview_bg", "desk"),
    "canvas_dot": ("canvas_dot", "grid", "grid_color", "canvas_grid"),
    "log_bg": ("log_bg", "console_bg", "terminal_bg", "code_bg", "log"),
    "log_fg": ("log_fg", "console_fg", "terminal_fg", "log_text", "code_fg"),
    "selection": ("selection", "selection_bg", "select_bg", "highlight_bg"),
    "selection_text": ("selection_text", "select_fg"),
    "white": ("white", "paper", "page_bg"),
    "shadow": ("shadow", "drop_shadow", "shadow_color"),
}

_THEME_MOD = None
_THEME_APPLIED = False


def _load_theme_module():
    """尽量拿到主题模块（每次重新 import，允许 theme.py 中途才出现）。"""
    global _THEME_MOD
    candidates = [os.path.join(HERE, "theme.py"),
                  os.path.join(HERE, "design", "theme.py")]
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            import importlib.util
            name = "pdf_layer_theme"
            spec = importlib.util.spec_from_file_location(name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "apply_theme"):
                _THEME_MOD = mod
                return mod
        except Exception:
            continue
    return None


def _as_palette(obj):
    """把主题模块的返回值尽量转成 {str: str} 的调色板。"""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return {k: v for k, v in obj.items() if isinstance(k, str)}
    out = {}
    try:
        for k, v in vars(obj).items():
            if isinstance(v, str):
                out[k] = v
    except Exception:
        pass
    return out


def apply_theme(root, dark=False):
    """应用 theme.py（若有）；返回规范键名的调色板 dict，theme 未就绪时用内置兜底。

    兼容两种主题接口：
      · apply_theme(root, dark=False) -> {"bg":..., "text":...} 平铺调色板；
      · apply_theme(root, dark=False) -> tokens（含嵌套 PALETTE / TK），如 theme.py。
    """
    global _THEME_APPLIED
    src = {}
    tk_tokens = {}
    theme_pal = {}
    mod = _THEME_MOD or _load_theme_module()
    applied = False
    if mod is not None and hasattr(mod, "apply_theme"):
        try:
            try:
                result = mod.apply_theme(root, dark=dark)
            except TypeError:
                result = mod.apply_theme(root, dark)
            if isinstance(result, dict):
                for key in ("PALETTE", "palette"):
                    if isinstance(result.get(key), dict):
                        theme_pal = result[key]
                        break
                for key in ("TK", "tk"):
                    if isinstance(result.get(key), dict):
                        tk_tokens = result[key]
                        break
                if theme_pal:
                    src = {k: v for k, v in theme_pal.items()
                           if isinstance(k, str) and isinstance(v, str)}
                else:
                    src = _as_palette(result)
            else:
                src = _as_palette(result)
            applied = True
        except Exception:
            src = {}
            applied = False
    base = dict(FALLBACK_DARK if dark else FALLBACK_LIGHT)
    for raw_key, val in src.items():
        if not isinstance(val, str) or not val:
            continue
        key = raw_key
        for canon, aliases in _PAL_ALIASES.items():
            if raw_key in aliases or raw_key == canon:
                key = canon
                break
        base[key] = val
    # 主题的 tk 原生控件令牌（canvas/text/log/paper/selection）映射到规范键
    tk_map = (("canvas_bg", "canvas"), ("input_bg", "card_alt"),
              ("text_bg", "card_alt"), ("log_text_bg", "log_bg"),
              ("select_bg", "selection"), ("select_fg", "selection_text"),
              ("paper", "white"))
    for raw, canon in tk_map:
        val = tk_tokens.get(raw)
        if isinstance(val, str) and val:
            base[canon] = val
    # 主题没有 log_fg 时：日志底色上用正文色；主色上的文字用 on_accent
    if isinstance(theme_pal.get("text"), str) and tk_tokens.get("log_text_bg"):
        base["log_fg"] = theme_pal["text"]
    if isinstance(theme_pal.get("on_accent"), str):
        base["accent_text"] = theme_pal["on_accent"]
        base["selection_text"] = theme_pal["on_accent"]
    if isinstance(theme_pal.get("shadow"), str):
        base["shadow"] = theme_pal["shadow"]
    base["dark"] = bool(dark)
    _THEME_APPLIED = bool(applied and (src or tk_tokens or theme_pal))
    return base


def theme_active():
    """当前是否由 theme.py 接管样式（供测试 / 状态栏判断）。"""
    return bool(_THEME_APPLIED)


def _mix(color_a, color_b, t):
    """两个 #rrggbb 颜色按 t（0=a，1=b）混合；解析失败返回 a。"""
    def _rgb(c):
        try:
            c = c.lstrip("#")
            if len(c) == 3:
                c = "".join(ch * 2 for ch in c)
            return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            return None
    a, b = _rgb(color_a), _rgb(color_b)
    if not a or not b:
        return color_a
    t = max(0.0, min(1.0, float(t)))
    return "#%02x%02x%02x" % tuple(int(round(a[i] + (b[i] - a[i]) * t)) for i in range(3))


# ================================================================ 几何 / 单测友好工具
def parse_geometry(geometry):
    """'1280x820+100+40' -> (1280, 820, 100, 40)；无效返回 None。"""
    if not geometry:
        return None
    m = re.match(r"^\s*(\d+)x(\d+)([+-]\d+)([+-]\d+)\s*$", str(geometry))
    if not m:
        return None
    try:
        w, h = int(m.group(1)), int(m.group(2))
        x, y = int(m.group(3)), int(m.group(4))
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return w, h, x, y


def format_geometry(w, h, x, y):
    """整数元组 -> Tk 几何串（负偏移保持符号）。"""
    return "%dx%d%+d%+d" % (int(w), int(h), int(x), int(y))


def default_geometry(screen_w, screen_h):
    """首次打开：屏幕 90%（居中偏上），同时保证小屏（1366×768）也能用。"""
    sw, sh = max(320, int(screen_w)), max(240, int(screen_h))
    w = min(max(1100, int(sw * 0.9)), max(320, sw - 40))
    h = min(max(700, int(sh * 0.9)), max(240, sh - 120))
    x = max(0, (sw - w) // 2)
    y = max(0, (sh - h) // 3)
    return format_geometry(w, h, x, y)


def clamp_geometry(geometry, screen_w, screen_h, min_w=640, min_h=480, bottom_reserve=80):
    """恢复上次几何：夹到屏幕内；无效返回 None。"""
    parsed = parse_geometry(geometry) if isinstance(geometry, str) else geometry
    if not parsed:
        return None
    w, h, x, y = parsed
    sw, sh = max(320.0, float(screen_w)), max(240.0, float(screen_h))
    max_w = max(320, int(sw) - 20)
    max_h = max(240, int(sh) - bottom_reserve)
    w = max(320, min(int(w), max_w))
    h = max(240, min(int(h), max_h))
    x = max(0, min(int(x), max(0, int(sw) - w)))
    y = max(0, min(int(y), max(0, int(sh) - h - 40)))
    return format_geometry(w, h, x, y)


def compute_fit_zoom(canvas_w, canvas_h, page_w_pt, page_h_pt,
                     scale=H.SCALE, margin=28, min_zoom=0.02, max_zoom=6.0):
    """整页适应：返回缩放（屏幕像素 / 页面源像素）。纯函数，便于单测。"""
    cw = max(1.0, float(canvas_w) - 2.0 * float(margin))
    ch = max(1.0, float(canvas_h) - 2.0 * float(margin))
    iw = max(1.0, float(page_w_pt) * float(scale))
    ih = max(1.0, float(page_h_pt) * float(scale))
    z = min(cw / iw, ch / ih)
    return max(float(min_zoom), min(float(max_zoom), z))


def normalize_rect(rect):
    """[l,t,r,b] -> 规范化（左<右、上<下）。"""
    l, t, r, b = [float(v) for v in rect]
    return min(l, r), min(t, b), max(l, r), max(t, b)


def clamp_rect_to_page(rect, page_w, page_h, min_w=20.0, min_h=12.0):
    """把矩形夹进页面内，并保证最小尺寸。"""
    pw, ph = max(1.0, float(page_w)), max(1.0, float(page_h))
    mw, mh = min(float(min_w), pw), min(float(min_h), ph)
    l, t, r, b = normalize_rect(rect)
    l = max(0.0, min(l, pw - mw))
    r = max(l + mw, min(r, pw))
    t = max(0.0, min(t, ph - mh))
    b = max(t + mh, min(b, ph))
    return [round(l, 2), round(t, 2), round(r, 2), round(b, 2)]


def move_rect(rect, dx_pt, dy_pt, page_w, page_h):
    """整体平移矩形（保持尺寸，夹在页面内）。"""
    l, t, r, b = normalize_rect(rect)
    w, h = max(1.0, r - l), max(1.0, b - t)
    pw, ph = max(w, float(page_w)), max(h, float(page_h))
    nl = max(0.0, min(l + float(dx_pt), pw - w))
    nt = max(0.0, min(t + float(dy_pt), ph - h))
    return [round(nl, 2), round(nt, 2), round(nl + w, 2), round(nt + h, 2)]


def resize_rect(rect, handle, dx_pt, dy_pt, page_w, page_h, min_w=24.0, min_h=14.0):
    """按 8 个手柄之一缩放矩形；handle 取 nw/n/ne/e/se/s/sw/w。"""
    l, t, r, b = normalize_rect(rect)
    handle = (handle or "").lower()
    if "w" in handle:
        l += float(dx_pt)
    if "e" in handle:
        r += float(dx_pt)
    if "n" in handle:
        t += float(dy_pt)
    if "s" in handle:
        b += float(dy_pt)
    return clamp_rect_to_page([l, t, r, b], page_w, page_h, min_w, min_h)


def page_to_screen(canvas_w, canvas_h, zoom, pan_x, pan_y,
                   page_w_pt, page_h_pt, x_pt, y_pt, scale=H.SCALE):
    """页面 pt -> 画布屏幕像素。"""
    iw, ih = max(1.0, page_w_pt * scale), max(1.0, page_h_pt * scale)
    cx = canvas_w / 2.0 + pan_x
    cy = canvas_h / 2.0 + pan_y
    return (cx + (x_pt * scale - iw / 2.0) * zoom,
            cy + (y_pt * scale - ih / 2.0) * zoom)


def screen_to_page(canvas_w, canvas_h, zoom, pan_x, pan_y,
                   page_w_pt, page_h_pt, x_screen, y_screen, scale=H.SCALE):
    """画布屏幕像素 -> 页面 pt（page_to_screen 的逆变换）。"""
    iw, ih = max(1.0, page_w_pt * scale), max(1.0, page_h_pt * scale)
    cx = canvas_w / 2.0 + pan_x
    cy = canvas_h / 2.0 + pan_y
    z = max(1e-6, float(zoom))
    return ((x_screen - cx) / z + iw / 2.0) / scale, ((y_screen - cy) / z + ih / 2.0) / scale


def _now_stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _fmt_pt(v):
    return ("%.1f" % v).rstrip("0").rstrip(".")


# ================================================================ 手写层合成
def make_hand_factory(render_cfg):
    """按渲染配置生成 hand 工厂（渲染线程里用，不碰 tkinter 控件）。

    render_cfg: {"font": 字体文件名/__HAN_OWN__, "variants": bool, "newsym": bool}
    默认字体「★ 我的手写体」永远走 symbols.make_han_hand()。
    """
    cfg = dict(render_cfg or {})
    font = cfg.get("font") or DEFAULT_FONT
    variants = bool(cfg.get("variants"))
    newsym = bool(cfg.get("newsym"))

    def factory(p):
        kw = dict(size_pt=float(p.size_pt), ink=tuple(p.ink),
                  jitter=float(p.jitter), seed=int(p.seed))
        if isinstance(font, str) and font.startswith("lib:"):
            return symbols.make_han_hand(library_id=font[4:], **kw)
        if font == HAN_OWN and symbols.han_available():
            return symbols.make_han_hand(**kw)
        fp = _font_path(font)
        if newsym and symbols.available():
            return symbols.make_hand(fp, **kw)
        return H.Hand(
            fp,
            math_font_path=_font_path(MATH_FONT_DEFAULT),
            symbol_font_path=(_font_path(MATH_FONT_DEFAULT)
                              if "ZTi/" in font else None),
            variant_fonts=(_variant_fonts(font) if variants else None),
            **kw)
    return factory


def compose_page(page, hand_factory=None):
    """背景 + 每段手写块 -> (整页 RGB 图, [(宽pt,高pt)...], 警告列表)。"""
    page._warn = []
    img = page.background().copy()
    used = []
    warns = []
    for i, p in enumerate(page.paragraphs):
        text = p.text or ""
        if not text.strip():
            p.used_size = (0.0, 0.0)
            used.append((0.0, 0.0))
            continue
        hand = PM.make_hand(p, hand_factory)
        block, w_pt, h_pt = hand.render_block(text, width_pt=p.width,
                                              line_pt=p.line_pt)
        p.used_size = (w_pt, h_pt)
        used.append((w_pt, h_pt))
        x = p.left
        if p.align == "center":
            x = p.left + (p.width - w_pt) / 2.0
        elif p.align == "right":
            x = p.right - w_pt
        img.alpha_composite(block, (int(round(x * H.SCALE)),
                                    int(round(p.top * H.SCALE))))
        if h_pt > p.height + 1.0:
            warns.append("第 %d 段超出框高（%.1f > %.1f pt），已照常绘制"
                         % (i + 1, h_pt, p.height))
    warns = list(page._warn) + warns
    return img.convert("RGB"), used, warns


def handwriting_layer(page, hand_factory=None):
    """只含手写笔迹的透明整页层（导出 PDF 时叠加到源 PDF 上）。"""
    size = page.background().size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for p in page.paragraphs:
        text = p.text or ""
        if not text.strip():
            continue
        hand = PM.make_hand(p, hand_factory)
        block, w_pt, _h = hand.render_block(text, width_pt=p.width,
                                            line_pt=p.line_pt)
        x = p.left
        if p.align == "center":
            x = p.left + (p.width - w_pt) / 2.0
        elif p.align == "right":
            x = p.right - w_pt
        img.alpha_composite(block, (int(round(x * H.SCALE)),
                                    int(round(p.top * H.SCALE))))
    return img


def export_pdf_with_factory(page, path, hand_factory=None):
    """导出 PDF：有源 PDF 时叠加到对应页（保留原内容）；否则新建页面。"""
    import io
    import pymupdf as fitz
    layer = handwriting_layer(page, hand_factory)
    buf = io.BytesIO()
    layer.save(buf, format="PNG")
    png = buf.getvalue()
    folder = os.path.dirname(os.path.abspath(path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    if page.source_pdf and os.path.exists(page.source_pdf):
        doc = fitz.open(page.source_pdf)
        idx = page.bg_page if 0 <= page.bg_page < len(doc) else 0
        pg = doc[idx]
        pg.insert_image(pg.rect, stream=png, overlay=True, keep_proportion=False)
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


_RESAMPLE = getattr(Image, "Resampling", Image).LANCZOS


# ================================================================ 侧栏滚动容器
class _ScrollFrame(_ttk.Frame if _ttk is not None else object):
    """竖向滚动容器（左侧卡片区，小屏 1366×768 也能用）。"""

    def __init__(self, master, bg="#f6f8fa"):
        super().__init__(master)
        self._canvas = _tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self._vsb = _ttk.Scrollbar(self, orient="vertical",
                                   command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y")
        self.inner = _ttk.Frame(self._canvas, style="Sidebar.TFrame")
        self._win = self._canvas.create_window((0, 0), window=self.inner,
                                               anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self._canvas.bind("<Configure>", self._on_canvas)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

    def _on_inner(self, _event=None):
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
            self._canvas.itemconfigure(self._win,
                                       width=max(1, self._canvas.winfo_width()))
        except Exception:
            pass

    def _on_canvas(self, event):
        try:
            self._canvas.itemconfigure(self._win, width=max(1, event.width))
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception:
            pass

    def _on_wheel(self, event):
        try:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass
        return "break"

    def scroll_wheel(self, event):
        return self._on_wheel(event)

    def set_bg(self, color):
        try:
            self._canvas.configure(bg=color)
        except Exception:
            pass


# ================================================================ 两行卡片式段落列表
class _ParagraphList(_ttk.Frame if _ttk is not None else object):
    """两行卡片式段落列表（Canvas 自绘）。

    第一行「编号. 摘要（单行省略）」+ 第二行 muted「字号 · 行距 · 对齐」，
    选中项 accent_soft 底色 + 左侧 3px 主色竖条；条目高约 46 逻辑 px。
    tk.Listbox 做不了双行/自绘，所以这里用 Canvas 画 + 自己命中测试。
    """

    def __init__(self, master, on_select=None, rows=3, dpi_scale=1.0):
        super().__init__(master)
        self.on_select = on_select
        self._items = []
        self._selected = None
        self._pal = {}
        self._rows = max(1, int(rows))
        self._dpi = max(1.0, float(dpi_scale or 1.0))
        self._title_font = None
        self._sub_font = None
        self._compact = True
        self._row_h = max(int(34 * self._dpi), 30)
        self._canvas = _tk.Canvas(self, highlightthickness=0, bd=0,
                                  height=self._row_h * self._rows + 2)
        self._vsb = _ttk.Scrollbar(self, orient="vertical",
                                   command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._vsb.pack(side="right", fill="y")
        self._canvas.bind("<Configure>", lambda e: self.redraw())
        self._canvas.bind("<Button-1>", self._on_click)
        self._canvas.bind("<MouseWheel>", self._on_wheel)

    # ---- 主题 / 字体
    def set_palette(self, pal, family):
        import tkinter.font as tkfont
        self._pal = dict(pal or {})
        try:
            self._title_font = tkfont.Font(family=family, size=10)
            self._sub_font = tkfont.Font(family=family, size=9)
        except Exception:
            self._title_font = self._sub_font = None
        self._relayout()
        self.redraw()

    def _relayout(self):
        """按「单行摘要 / 双行摘要」重算行高与画布高度。"""
        try:
            lh = self._title_font.metrics("linespace") if self._title_font else 18
        except Exception:
            lh = 18
        if self._compact:
            height = max(28, lh + int(13 * self._dpi))
        else:
            try:
                sh = self._sub_font.metrics("linespace") if self._sub_font else 16
            except Exception:
                sh = 16
            height = max(int(46 * self._dpi), lh + sh + int(12 * self._dpi))
        self._row_h = int(height)
        n = len(self._items)
        visible = self._rows if n == 0 else min(self._rows, max(1, n))
        self._visible = visible
        try:
            self._canvas.configure(
                bg=self._pal.get("card", "#FFFCF4"),
                height=self._row_h * visible + 2)
        except Exception:
            pass

    # ---- 数据 / 选中
    def set_items(self, items, selected=None):
        self._items = list(items or [])
        self._compact = not any((sub or "").strip() for _t, sub in self._items)
        if selected is not None:
            self._selected = int(selected) if 0 <= int(selected) < len(self._items) else None
        elif self._selected is not None and self._selected >= len(self._items):
            self._selected = None
        self._relayout()
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
            w = max(80, c.winfo_width())
            pal = self._pal or {}
            card = pal.get("card", "#ffffff")
            alt = pal.get("card_alt", "#f1f4f8")
            border = pal.get("border", "#d5dbe3")
            text = pal.get("text", "#1f2328")
            muted = pal.get("muted", "#6b7280")
            accent = pal.get("accent", "#2563eb")
            soft = pal.get("accent_soft") or _mix(card, accent, 0.10)
            accent_text = pal.get("accent_text", "#ffffff")
            for i, (title, sub) in enumerate(self._items):
                y = i * self._row_h
                sel = (i == self._selected)
                fill = soft if sel else alt
                c.create_rectangle(4, y + 2, w - 10, y + self._row_h - 2,
                                   fill=fill,
                                   outline=(accent if sel else border),
                                   width=1)
                if sel:
                    c.create_rectangle(4, y + 2, 7, y + self._row_h - 2,
                                       fill=accent, outline="")
                tx = 15
                maxw = max(40, w - 28)
                title_font = self._title_font
                sub_font = self._sub_font
                try:
                    line_h = (title_font.metrics("linespace")
                              if title_font else 18)
                except Exception:
                    line_h = 18
                if self._compact or not (sub or "").strip():
                    ty = y + (self._row_h - line_h) / 2.0
                    c.create_text(tx, ty, anchor="nw",
                                  text=self._trim(title, title_font, maxw),
                                  font=title_font, fill=text)
                else:
                    ty = y + max(4, int(5 * self._dpi))
                    c.create_text(tx, ty, anchor="nw",
                                  text=self._trim(title, title_font, maxw),
                                  font=title_font, fill=text)
                    c.create_text(tx, ty + line_h + 1, anchor="nw",
                                  text=self._trim(sub, sub_font, maxw),
                                  font=sub_font, fill=muted)
            total = max(1, self._row_h * max(1, len(self._items)))
            c.configure(scrollregion=(0, 0, w, total))
            if self._selected is not None and total > 1:
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

    # ---- 交互
    def _index_at(self, y_screen):
        try:
            y = self._canvas.canvasy(y_screen)
        except Exception:
            y = y_screen
        idx = int(y // max(1, self._row_h))
        return idx if 0 <= idx < len(self._items) else None

    def _on_click(self, event):
        idx = self._index_at(event.y)
        if idx is not None:
            self.select(idx, notify=True)

    def _on_wheel(self, event):
        try:
            self._canvas.yview_scroll(int(-event.delta / 120), "units")
        except Exception:
            pass
        return "break"


# ================================================================ App
class App:
    """主窗口。headless 用法：root = tk.Tk(); root.withdraw(); App(root)。"""

    def __init__(self, root, config_path=None, auto_render=True, dark=None):
        if _tk is None:
            raise RuntimeError("没有 tkinter，无法启动图形界面。")
        self.tk, self.ttk = _tk, _ttk
        self.root = root
        self.config_path = config_path or CONFIG_PATH
        self.auto_render = bool(auto_render)
        self.closing = False
        self._timers = []
        self._render_after = None
        self._text_after = None
        self._param_after = None
        self._last_geometry = ""
        self._default_geom = ""

        # ---- 页面模型（唯一真相源：背景 + 多段落）----
        self.page = PM.Page()
        self.page.add_paragraph(PM.Paragraph(
            text=SAMPLE_TEXT, rect=[56.0, 90.0, 539.0, 640.0]))
        self.page_image = None
        self._coverage = ""
        self.render_token = 0
        self._results = queue.Queue()
        self._render_active = False
        self._poll_after = None
        self._ai_queue = queue.Queue()
        self._ai_poll_left = 0
        self.selected = 0
        self.mode = "select"          # select / draw
        self.show_frames = True       # 画布上是否画段落矩形
        self._interact = None
        self._space_down = False
        self._syncing = False
        self._photo = None
        self._photo_key = None
        self.view_zoom = 0.0
        self.fit_mode = True
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._canvas_size_override = None
        self._hover_cursor = ""
        self._last_status = ""
        self._theme_hint = ""

        # ---- 渲染参数（段落参数在 page.paragraphs 里；这里存新段落默认值）----
        self.cfg = {
            "font": DEFAULT_FONT,
            "size": H.DEFAULT_SIZE_PT,
            "line": H.DEFAULT_LINE_PT,
            "jitter": 1.0,
            "seed": 20261118,
            "ink": tuple(H.INK),
            "variants": False,
            "newsym": symbols.available(),
        }

        # ---- AI 接口 ----
        self.ai_proc = None
        self.ai_port = AI_DEFAULT_PORT
        self._ai_logf = None
        self._ai_status_text = "AI：未启动"
        self._ai_status_display = "未启动"

        # ---- 配置 / 主题 ----
        self._config = self._read_config()
        self.dark = bool(self._config.get("dark") if dark is None else dark)
        font_cfg = self._config.get("font")
        if font_cfg and any(f == font_cfg for _lab, f in FONT_LABELS):
            self.cfg["font"] = font_cfg

        root.title("手写体生成器")
        self._apply_window_geometry()

        self._setup_fonts()
        try:
            self._dpi_scale = max(1.0, float(self.root.winfo_fpixels("1i")) / 96.0)
        except Exception:
            self._dpi_scale = 1.0
        self._tag_font = None
        self._toolbar_specs = []
        self._toolbar_compact = False
        self._toolbar_full_req = 0
        self._toolbar_compact_req = 0
        self._float_outer = None
        self._float_window = None
        self.pal = apply_theme(root, dark=self.dark)
        self._theme_hint = "theme.py" if theme_active() else "内置兜底"
        self._setup_styles()

        self._build_ui()
        self._apply_tk_colors()
        self._restore_ui_state()
        self._bind_global()
        self._timed(80, self._init_toolbar_state)

        self.log("就绪。示例文字已载入，点右上「渲染」更新预览。")
        if DEFAULT_FONT == HAN_OWN:
            self.log("字体：★ 我的手写体（汉字 ZTi-Han + 符号 ZTi-Sym）")
        if self.auto_render:
            self._render_after = self._timed(250, self._start_render_thread)

    # ============================================================ 窗口 / 配置
    def _read_config(self):
        try:
            with open(self.config_path, encoding="utf-8") as fp:
                data = json.load(fp)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _apply_window_geometry(self):
        try:
            sw = int(self.root.winfo_screenwidth())
            sh = int(self.root.winfo_screenheight())
        except Exception:
            sw, sh = 1366, 768
        min_w = max(640, min(1100, sw - 40))
        min_h = max(480, min(700, sh - 140))
        try:
            self.root.minsize(min_w, min_h)
        except Exception:
            pass
        geom = clamp_geometry(self._config.get("geometry"), sw, sh,
                              min_w=min_w, min_h=min_h)
        if not geom:
            geom = default_geometry(sw, sh)
        self._default_geom = geom
        self._last_geometry = geom
        try:
            self.root.geometry(geom)
        except Exception:
            pass

    def get_geometry_string(self):
        """当前窗口几何串（隐藏窗口取不到时回退到请求值）。"""
        live = ""
        try:
            self.root.update_idletasks()
            live = self.root.winfo_geometry()
        except Exception:
            live = ""
        parsed = parse_geometry(live)
        if parsed and parsed[0] >= 200 and parsed[1] >= 150:
            self._last_geometry = live
            return live
        return self._last_geometry or self._default_geom or ""

    def save_config(self, path=None):
        """保存窗口几何 / 主题 / 字体到 JSON；返回写出路径（失败返回 None）。"""
        path = path or self.config_path
        data = {
            "geometry": self.get_geometry_string(),
            "dark": bool(self.dark),
            "font": self.cfg.get("font") or DEFAULT_FONT,
            "page": getattr(self, "page_key", None) or "write",
            "updated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            return path
        except Exception:
            try:
                self.log("保存配置失败：\n" + traceback.format_exc())
            except Exception:
                pass
            return None

    def _cancel_all_timers(self):
        for aid in list(self._timers):
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
        self._timers = []
        for attr in ("_render_after", "_text_after", "_param_after",
                     "_poll_after", "_sash_after"):
            self._cancel(attr)

    def _on_close(self):
        if self.closing:
            return
        self.closing = True
        self._cancel_all_timers()
        self.save_config()
        try:
            if self.ai_proc is not None and self.ai_proc.poll() is None:
                self.log("AI 接口进程仍在运行（pid %s），退出 GUI 不关闭它。"
                         % self.ai_proc.pid)
        except Exception:
            pass
        try:
            if self._ai_logf:
                self._ai_logf.close()
                self._ai_logf = None
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    def close(self):
        """公开的关闭入口（测试 / 宿主程序用）：取消定时器 + 存配置 + 销毁窗口。"""
        self._on_close()

    # ============================================================ 字体 / 样式
    def _setup_fonts(self):
        """中文 UI 字体 + DPI 缩放（tk scaling）。"""
        try:
            import tkinter.font as tkfont
            scale = float(self.root.winfo_fpixels("1i")) / 72.0
            cur = float(self.root.tk.call("tk", "scaling"))
            if scale > 0 and abs(scale - cur) > 0.05:
                self.root.tk.call("tk", "scaling", scale)
            families = set(tkfont.families(self.root))
            family = next((f for f in ("Microsoft YaHei UI", "Microsoft YaHei",
                                       "微软雅黑", "Segoe UI", "Arial")
                           if f in families), None)
            if family:
                for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont",
                             "TkHeadingFont", "TkTooltipFont"):
                    try:
                        f = tkfont.nametofont(name)
                        if f.actual("family") != family:
                            f.configure(family=family)
                    except Exception:
                        pass
            self._ui_family = family or "TkDefaultFont"
            self._mono_family = next((f for f in ("Consolas", "Cascadia Mono",
                                                  "Courier New")
                                      if f in families), self._ui_family)
        except Exception:
            self._ui_family = "TkDefaultFont"
            self._mono_family = "TkFixedFont"

    def _setup_styles(self):
        """theme.py 未就绪时自建命名样式，避免默认 gray95 观感。"""
        if not theme_active():
            self._setup_fallback_styles()
        # 主题给按钮样式设了固定宽度（-11 字符），这里统一改回「按文字自适应」。
        # 另外补两个本模块专用样式：描边危险按钮 / 浅底卡片按钮（theme.py 里也有，
        # 这里按当前的 pal 再刷一遍，保证切深浅色后仍是新颜色）。
        pal = self.pal
        try:
            st = self.ttk.Style()
            for name in ("TButton", "Ghost.TButton", "Accent.TButton",
                         "Danger.TButton", "Tool.TButton", "Section.TButton",
                         "Soft.TButton", "DangerOutline.TButton"):
                st.configure(name, width=0)
            st.configure("DangerOutline.TButton",
                         background=pal["card"], foreground=pal["danger"],
                         bordercolor=pal["danger"], lightcolor=pal["danger"],
                         darkcolor=pal["danger"], borderwidth=1,
                         relief="solid", padding=(8, 4), focusthickness=0)
            st.map("DangerOutline.TButton",
                   background=[("pressed", pal["danger_soft"]),
                               ("active", pal["danger_soft"]),
                               ("disabled", pal["card_alt"])],
                   foreground=[("active", pal["danger_hover"]),
                               ("pressed", pal["danger_hover"]),
                               ("disabled", pal["muted"])],
                   bordercolor=[("active", pal["danger_hover"]),
                                ("pressed", pal["danger_hover"])])
            st.configure("Soft.TButton",
                         background=pal["card_alt"], foreground=pal["text"],
                         bordercolor=pal["border"], lightcolor=pal["border"],
                         darkcolor=pal["border"], borderwidth=1,
                         relief="solid", padding=(6, 6), focusthickness=0)
            st.map("Soft.TButton",
                   background=[("pressed", pal["pressed"]),
                               ("active", pal["hover"])],
                   foreground=[("active", pal["text"])],
                   bordercolor=[("active", pal["accent"])])
        except Exception:
            pass

    def _setup_fallback_styles(self):
        pal = self.pal
        st = self.ttk.Style()
        try:
            if "clam" in st.theme_names():
                st.theme_use("clam")
        except Exception:
            pass
        fam = getattr(self, "_ui_family", "TkDefaultFont")

        def btn(name, bg, fg, bord, padding=(8, 4), hover=None):
            st.configure(name, background=bg, foreground=fg, bordercolor=bord,
                         focuscolor=bg, focusthickness=0, padding=padding,
                         relief="flat")
            st.map(name,
                   background=[("active", hover or bg), ("pressed", hover or bg),
                               ("disabled", pal["card_alt"])],
                   foreground=[("disabled", pal["muted"])],
                   bordercolor=[("active", bord or bg)])

        try:
            st.configure(".", background=pal["bg"], foreground=pal["text"])
            st.configure("TFrame", background=pal["bg"])
            st.configure("TLabel", background=pal["bg"], foreground=pal["text"])
            st.configure("Card.TFrame", background=pal["card"])
            st.configure("Card.TLabel", background=pal["card"],
                         foreground=pal["text"])
            st.configure("Sidebar.TFrame", background=pal["sidebar"])
            st.configure("Sidebar.TLabel", background=pal["sidebar"],
                         foreground=pal["text"])
            st.configure("Toolbar.TFrame", background=pal["panel"])
            st.configure("Status.TFrame", background=pal["panel"])
            st.configure("SectionHeader.TLabel", background=pal["card"],
                         foreground=pal["text"], font=(fam, 10, "bold"))
            st.configure("Card.SectionHeader.TLabel", background=pal["card"],
                         foreground=pal["text"], font=(fam, 10, "bold"))
            st.configure("Sidebar.SectionHeader.TLabel",
                         background=pal["sidebar"], foreground=pal["text"],
                         font=(fam, 10, "bold"))
            st.configure("Muted.TLabel", background=pal["card"],
                         foreground=pal["muted"], font=(fam, 9))
            st.configure("Card.Muted.TLabel", background=pal["card"],
                         foreground=pal["muted"], font=(fam, 9))
            st.configure("Sidebar.Muted.TLabel", background=pal["sidebar"],
                         foreground=pal["muted"], font=(fam, 9))
            st.configure("Toolbar.Muted.TLabel", background=pal["panel"],
                         foreground=pal["muted"], font=(fam, 9))
            st.configure("Status.TFrame", background=pal["panel"])
            st.configure("Status.TLabel", background=pal["panel"],
                         foreground=pal["text"], font=(fam, 9))
            st.configure("Toolbar.TLabel", background=pal["panel"],
                         foreground=pal["text"])
            btn("Accent.TButton", pal["accent"], pal["accent_text"],
                pal["accent"], (10, 5), pal["accent_hover"])
            btn("Ghost.TButton", pal["card_alt"], pal["text"], pal["border"],
                (7, 4), pal["selection"])
            btn("Danger.TButton", pal["danger"], pal["white"], pal["danger"],
                (7, 4), pal["danger_hover"])
            st.configure("TEntry", fieldbackground=pal["card_alt"],
                         foreground=pal["text"], bordercolor=pal["border"],
                         insertcolor=pal["text"], padding=3)
            st.configure("TCombobox", fieldbackground=pal["card_alt"],
                         background=pal["card_alt"], foreground=pal["text"],
                         arrowcolor=pal["text"], bordercolor=pal["border"],
                         padding=3)
            st.map("TCombobox", fieldbackground=[("readonly", pal["card_alt"])],
                   foreground=[("readonly", pal["text"])])
            st.configure("TSpinbox", fieldbackground=pal["card_alt"],
                         foreground=pal["text"], arrowcolor=pal["text"],
                         bordercolor=pal["border"], padding=3)
            st.configure("TCheckbutton", background=pal["card"],
                         foreground=pal["text"], indicatorcolor=pal["card_alt"])
            st.configure("Card.TCheckbutton", background=pal["card"],
                         foreground=pal["text"], indicatorcolor=pal["card_alt"])
            st.configure("Sidebar.TCheckbutton", background=pal["sidebar"],
                         foreground=pal["text"],
                         indicatorcolor=pal["card_alt"])
            st.map("TCheckbutton", background=[("active", pal["card"])],
                   foreground=[("active", pal["text"])])
            st.configure("TRadiobutton", background=pal["card"],
                         foreground=pal["text"])
            st.configure("TScrollbar", background=pal["border"],
                         troughcolor=pal["card"], arrowcolor=pal["text"],
                         bordercolor=pal["card"], relief="flat")
            st.configure("TSeparator", background=pal["border"])
            st.configure("TPanedwindow", background=pal["bg"])
            st.configure("Sash", background=pal["border"], gripcount=0)
            # 卡片（Labelframe）：与 theme.py 的 Card.TLabelframe 对齐
            st.configure("Card.TLabelframe", background=pal["card"],
                         bordercolor=pal["border"], borderwidth=1,
                         relief="solid", padding=(12, 10))
            st.configure("Card.TLabelframe.Label", background=pal["card"],
                         foreground=pal["text"], font=(fam, 10, "bold"),
                         padding=(0, 0, 6, 6))
            st.configure("TLabelframe", background=pal["card"],
                         bordercolor=pal["border"], borderwidth=1,
                         relief="solid")
            st.configure("TLabelframe.Label", background=pal["card"],
                         foreground=pal["text"], font=(fam, 10, "bold"))
            st.configure("TNotebook", background=pal["bg"],
                         bordercolor=pal["border"])
            st.configure("TNotebook.Tab", background=pal["card_alt"],
                         foreground=pal["muted"], padding=(10, 4))
            st.map("TNotebook.Tab",
                   background=[("selected", pal["card"])],
                   foreground=[("selected", pal["text"])])
        except Exception:
            pass

    def _apply_tk_colors(self):
        """tk 原生控件（Canvas/Text/Listbox/顶栏按钮）跟主题走。"""
        pal = self.pal
        try:
            self.root.configure(bg=pal["bg"])
        except Exception:
            pass
        if hasattr(self, "_scroll"):
            self._scroll.set_bg(pal["sidebar"])
        try:
            self.canvas.configure(bg=pal["canvas"], highlightthickness=0)
        except Exception:
            pass
        try:
            self.log_text.configure(bg=pal["card"], fg=pal["text"],
                                    insertbackground=pal["text"],
                                    selectbackground=pal["selection"],
                                    selectforeground=pal["selection_text"])
        except Exception:
            pass
        try:
            self.text_widget.configure(bg=pal["card"], fg=pal["text"],
                                       insertbackground=pal["text"],
                                       selectbackground=pal["selection"],
                                       selectforeground=pal["selection_text"],
                                       relief="flat", bd=0,
                                       highlightthickness=1,
                                       highlightbackground=pal["border"],
                                       highlightcolor=pal["accent"])
        except Exception:
            pass
        try:
            self.para_list.set_palette(pal, getattr(self, "_ui_family",
                                                     "TkDefaultFont"))
        except Exception:
            pass
        try:
            if self._float_outer is not None:
                self._float_outer.configure(bg=pal["border"])
                self._float_inner.configure(style="Toolbar.TFrame")
        except Exception:
            pass
        # 顶栏上的 tk 按钮（品牌标 / ⋯ / 三页）不吃 ttk 样式，要手动刷色
        for attr, fg in (("brand_mark", pal["accent"]), ("more_btn", pal["muted"])):
            w = getattr(self, attr, None)
            if w is not None:
                try:
                    w.configure(bg=pal["toolbar"], fg=fg,
                                activebackground=pal["hover"],
                                activeforeground=pal["text"])
                except Exception:
                    pass
        try:
            self._update_nav()
        except Exception:
            pass
        try:
            self._draw_wave()
        except Exception:
            pass
        try:
            for dot in list(getattr(self, "_ai_dots", [])):
                dot.configure(bg=pal["card"], fg=pal.get("muted", "#7B7061"))
        except Exception:
            pass

    def set_dark(self, dark):
        self.dark = bool(dark)
        self.pal = apply_theme(self.root, dark=self.dark)
        self._theme_hint = "theme.py" if theme_active() else "内置兜底"
        self._setup_styles()
        self._apply_tk_colors()
        self._redraw_view()

    def toggle_dark(self):
        self.set_dark(not self.dark)

    # ============================================================ 定时器 / 线程
    def _timed(self, delay_ms, func):
        if self.closing:
            return None
        try:
            aid = self.root.after(int(delay_ms), func)
            self._timers.append(aid)
            return aid
        except Exception:
            return None

    def _safe_after(self, delay_ms, func):
        """子线程 -> 主线程；窗口已销毁时静默丢弃。"""
        try:
            if self.closing:
                return
            self.root.after(int(delay_ms), func)
        except Exception:
            pass

    def _cancel(self, attr):
        aid = getattr(self, attr, None)
        if aid:
            try:
                self.root.after_cancel(aid)
            except Exception:
                pass
            setattr(self, attr, None)

    # ============================================================ UI 骨架
    # 三个页面共用同一块画布，只切换左侧工作台：
    #   书写 —— 段落 / 正文 / 背景       样式 —— 字体 / 墨色与笔迹 / 高级
    #   输出 —— 导出 / 项目 / AI / 日志
    # 次要动作收进「⋯」菜单，说明性小字一律不进界面（放到「快捷键」里）。
    PAGES = (("write", "书写"), ("style", "样式"), ("out", "输出"))
    SASH_X = 336                 # 左侧工作台宽度（逻辑 px，按 DPI 放大）
    TOOLBAR_FULL_MIN = 900       # 兼容旧接口；新版顶栏固定单行

    def _sash_px(self):
        return max(300, int(round(self.SASH_X * getattr(self, "_dpi_scale", 1.0))))

    def _build_ui(self):
        ttk = self.ttk
        self._outer = ttk.Frame(self.root, style="TFrame")
        self._outer.pack(fill="both", expand=True)
        self._build_toolbar(self._outer)
        self._build_wave(self._outer)
        self._build_statusbar(self._outer)
        self._build_console(self._outer)            # 日志抽屉：默认收起

        self.paned = ttk.PanedWindow(self._outer, orient="horizontal")
        self.paned.pack(fill="both", expand=True)

        left = ttk.Frame(self.paned, style="Sidebar.TFrame")
        self.paned.add(left, weight=0)
        self._scroll = _ScrollFrame(left, bg=self.pal["sidebar"])
        self._scroll.pack(fill="both", expand=True)
        host = self._scroll.inner
        self._panels = {}
        for key, _label in self.PAGES:
            self._panels[key] = ttk.Frame(host, style="Sidebar.TFrame")
        self._build_write_panel(self._panels["write"])
        self._build_style_panel(self._panels["style"])
        self._build_out_panel(self._panels["out"])

        right = ttk.Frame(self.paned, style="Card.TFrame")
        self.paned.add(right, weight=1)
        self._build_canvas(right)

        self.page_key = None
        self.show_page(self._config.get("page") or "write")

        self._sash_done = False
        self._sash_tries = 0
        self._sash_after = self._timed(30, self._set_sash)
        self.paned.bind("<Configure>", self._on_paned_configure, add="+")

    def _on_paned_configure(self, event):
        """窗口第一次真正布局后把左侧栏宽度定下来（隐藏窗口里 sashpos 会丢）。"""
        if self._sash_done or self.closing:
            return
        if event.width < 500:
            return
        try:
            self.paned.sashpos(0, self._sash_px())
            if self.paned.sashpos(0) >= 240:
                self._sash_done = True
                self._bind_sidebar_wheel(self._scroll.inner)
                self._update_wraplengths()
        except Exception:
            pass

    def _set_sash(self):
        self._sash_after = None
        if self.closing:
            return
        try:
            self.paned.sashpos(0, self._sash_px())
            self._sash_done = self.paned.sashpos(0) >= 240
        except Exception:
            self._sash_done = False
        if self._sash_done:
            self._bind_sidebar_wheel(self._scroll.inner)
            self._update_wraplengths()
            return
        self._sash_tries += 1
        if self._sash_tries < 30:
            self._sash_after = self._timed(60, self._set_sash)

    def _update_wraplengths(self):
        """左侧栏宽度变化时重算换行宽度（只剩页信息一处长文本）。"""
        try:
            w = self._scroll.inner.winfo_width()
        except Exception:
            w = 0
        if w < 180:
            return
        wrap = max(140, int(w) - 40)
        lbl = getattr(self, "page_info", None)
        if lbl is not None:
            try:
                lbl.configure(wraplength=wrap)
            except Exception:
                pass

    def _bind_sidebar_wheel(self, widget):
        """左侧卡片区滚轮：不用 bind_all（避免和画布缩放撞车），逐个控件绑。"""
        try:
            if not isinstance(widget, (self.tk.Text, self.tk.Entry,
                                       self.ttk.Entry, self.ttk.Combobox,
                                       self.ttk.Spinbox)):
                widget.bind("<MouseWheel>", self._scroll.scroll_wheel, add="+")
            for child in widget.winfo_children():
                self._bind_sidebar_wheel(child)
        except Exception:
            pass

    # ------------------------------------------------------------ 顶栏
    def _build_toolbar(self, parent):
        """顶栏：品牌 + 三个页面 + 渲染 / ⋯。"""

        tk, ttk = self.tk, self.ttk
        pal = self.pal
        fam = getattr(self, "_ui_family", "TkDefaultFont")
        outer = ttk.Frame(parent, style="Toolbar.TFrame")
        outer.pack(side="top", fill="x")
        self.toolbar = outer
        self._page_count_labels = []
        self._bg_labels = []
        self._page_entries = []
        self._ai_status_labels = []
        self._ai_dots = []
        self.var_bg_page = tk.IntVar(value=1)

        brand = ttk.Frame(outer, style="Toolbar.TFrame")
        brand.pack(side="left", padx=(16, 2), pady=(7, 6))
        self.brand_mark = tk.Label(brand, text="≈", bd=0, padx=0, pady=0,
                                   fg=pal["accent"], bg=pal["toolbar"],
                                   font=(fam, 18, "bold"))
        self.brand_mark.pack(side="left", padx=(0, 5))
        self.brand_text = ttk.Label(brand, text="手写体生成器",
                                    style="Brand.TLabel")
        self.brand_text.pack(side="left")

        nav = ttk.Frame(outer, style="Toolbar.TFrame")
        nav.pack(side="left", padx=(20, 0), pady=9)
        self._nav = nav
        self._nav_buttons = {}
        for key, label in self.PAGES:
            b = tk.Button(nav, text=label, bd=0, highlightthickness=0,
                          relief="flat", cursor="hand2", padx=15, pady=4,
                          font=(fam, 10), bg=pal["toolbar"], fg=pal["muted"],
                          activebackground=pal["accent_soft"],
                          activeforeground=pal["accent"],
                          command=lambda k=key: self.show_page(k))
            b.pack(side="left", padx=(0, 3))
            b.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
            b.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self._nav_buttons[key] = b

        right = ttk.Frame(outer, style="Toolbar.TFrame")
        right.pack(side="right", padx=(0, 14))
        self.render_btn = ttk.Button(right, text="渲染",
                                     command=self.render_async,
                                     style="Accent.TButton", width=0)
        self.render_btn.pack(side="left")
        self.more_btn = tk.Button(right, text="⋯", bd=0, highlightthickness=0,
                                  relief="flat", cursor="hand2", padx=9, pady=0,
                                  font=(fam, 15), bg=pal["toolbar"],
                                  fg=pal["muted"],
                                  activebackground=pal["hover"],
                                  activeforeground=pal["text"],
                                  command=self._show_more_menu)
        self.more_btn.pack(side="left", padx=(6, 0))
        self.theme_btn = None          # 深浅色在「⋯」菜单里，旧属性保留

    def _build_wave(self, parent):
        """顶栏下沿的一道浪（沙滩主题的识别点）。"""
        tk = self.tk
        self._wave = tk.Canvas(parent, height=11, bd=0, highlightthickness=0,
                               bg=self.pal["accent_soft"])
        self._wave.pack(side="top", fill="x")
        self._wave.bind("<Configure>", lambda e: self._draw_wave())
        self._timed(40, self._draw_wave)

    def _draw_wave(self):
        c = getattr(self, "_wave", None)
        if c is None:
            return
        try:
            w = max(240, int(c.winfo_width()))
        except Exception:
            w = 240
        pal = self.pal
        try:
            c.delete("all")
            c.configure(bg=pal["accent_soft"], bd=0, highlightthickness=0)
            top = pal["toolbar"]
            pts = []
            x = -8
            while x <= w + 12:
                pts += [x, 5.0 + 2.6 * math.sin(x / 82.0 * 2 * math.pi + 0.9)]
                x += 7
            c.create_polygon(pts + [w + 12, -2, -8, -2], fill=top, outline="")
            c.create_line(pts, fill=pal["accent"], width=1, smooth=True)
            pts2 = []
            x = -8
            while x <= w + 12:
                pts2 += [x, 8.2 + 1.5 * math.sin(x / 61.0 * 2 * math.pi + 2.6)]
                x += 7
            c.create_line(pts2, fill=_mix(pal["accent_soft"], pal["accent"], 0.45),
                          width=1, smooth=True)
        except Exception:
            pass

    # ---- 页面切换
    def _nav_hover(self, key, inside):
        b = getattr(self, "_nav_buttons", {}).get(key)
        if b is None or key == getattr(self, "page_key", None):
            return
        try:
            b.configure(bg=self.pal["hover"] if inside else self.pal["toolbar"])
        except Exception:
            pass

    def _update_nav(self):
        fam = getattr(self, "_ui_family", "TkDefaultFont")
        for key, b in getattr(self, "_nav_buttons", {}).items():
            active = (key == self.page_key)
            try:
                b.configure(
                    bg=self.pal["accent_soft"] if active else self.pal["toolbar"],
                    fg=self.pal["accent"] if active else self.pal["muted"],
                    font=(fam, 10, "bold") if active else (fam, 10))
            except Exception:
                pass

    def show_page(self, key):
        """切换左侧工作台（画布不动，避免重建与缩放丢失）。"""
        if key not in getattr(self, "_panels", {}):
            key = "write"
        self.page_key = key
        for frame in self._panels.values():
            try:
                frame.pack_forget()
            except Exception:
                pass
        try:
            self._panels[key].pack(fill="both", expand=True, pady=(10, 0))
        except Exception:
            return
        self._update_nav()
        try:
            self._scroll._canvas.yview_moveto(0.0)
        except Exception:
            pass

    def _init_toolbar_state(self):
        """兼容旧接口：顶栏固定单行，只在窄窗时收起品牌文字。"""
        if self.closing:
            return
        self._set_toolbar_compact(False)

    def _set_toolbar_compact(self, compact):
        self._toolbar_compact = bool(compact)
        try:
            if compact:
                self.brand_text.pack_forget()
            elif not self.brand_text.winfo_ismapped():
                self.brand_text.pack(side="left")
            self.root.update_idletasks()
        except Exception:
            pass

    def _update_toolbar_dynamic_labels(self):
        bg_txt = self._bg_label_text(self._toolbar_compact)
        for lbl in list(getattr(self, "_bg_labels", [])):
            try:
                lbl.configure(text=bg_txt)
            except Exception:
                pass
        disp = getattr(self, "_ai_status_display", "未启动")
        for lbl in list(getattr(self, "_ai_status_labels", [])):
            try:
                lbl.configure(text=disp)
            except Exception:
                pass

    def _page_count_text(self):
        if self.page.bg_kind == "pdf":
            return "%d/%d 页" % (self.page.bg_page + 1, self.bg_pdf_pages or 1)
        return "1/1 页"

    def _bg_label_text(self, compact=False):
        if self.page.bg_kind == "pdf":
            return "PDF 背景  ▾"
        if self.page.bg_kind == "image":
            return "图片背景  ▾"
        return "空白 A4  ▾"

    # ------------------------------------------------------------ 菜单
    def _menu(self):
        """统一配色的弹出菜单（沙滩配色，不跟随 Windows 原生灰）。"""
        tk = self.tk
        fam = getattr(self, "_ui_family", "TkDefaultFont")
        return tk.Menu(self.root, tearoff=0, bd=0, relief="flat",
                       bg=self.pal["card"], fg=self.pal["text"],
                       activebackground=self.pal["accent_soft"],
                       activeforeground=self.pal["accent"],
                       disabledforeground=self.pal["faint"],
                       selectcolor=self.pal["accent"],
                       font=(fam, 10))

    def _popup_menu(self, menu, widget):
        try:
            x = widget.winfo_rootx()
            y = widget.winfo_rooty() + widget.winfo_height() + 2
            menu.tk_popup(x, y)
        except Exception:
            pass
        finally:
            try:
                menu.grab_release()
            except Exception:
                pass

    def _show_more_menu(self):
        m = self._menu()
        m.add_command(label="保存项目…", command=self._dlg_save_project)
        m.add_command(label="打开项目…", command=self._dlg_open_project)
        m.add_separator()
        m.add_command(label="导入图片背景…", command=self._dlg_open_image)
        m.add_command(label="导入 PDF 背景…", command=self._dlg_import_pdf)
        if self.page.bg_kind == "pdf":
            m.add_command(label="上一页", command=self.bg_prev_page)
            m.add_command(label="下一页", command=self.bg_next_page)
        m.add_separator()
        m.add_command(label="切换深 / 浅色",
                      command=self.toggle_dark)
        m.add_command(label="日志 / 控制台", command=self.toggle_console)
        m.add_command(label="启动 AI 接口", command=self.launch_ai)
        m.add_separator()
        m.add_command(label="快捷键与手势", command=self._show_about)
        self._popup_menu(m, self.more_btn)

    def _show_bg_menu(self):
        m = self._menu()
        m.add_command(label="导入图片…", command=self._dlg_open_image)
        m.add_command(label="导入 PDF…", command=self._dlg_import_pdf)
        m.add_separator()
        m.add_command(label="空白 A4", command=self.set_blank_background)
        m.add_command(label="清除背景", command=self.clear_background)
        if self.page.bg_kind == "pdf":
            m.add_separator()
            m.add_command(label="上一页", command=self.bg_prev_page)
            m.add_command(label="下一页", command=self.bg_next_page)
        self._popup_menu(m, self.bg_btn)

    def _show_about(self):
        from tkinter import messagebox
        messagebox.showinfo(
            "快捷键与手势",
            "滚轮　　　　　以光标为中心缩放\n"
            "中键 / 空格拖动　平移画布\n"
            "双击　　　　　适应窗口\n"
            "纸面空白处拖框　重设当前段区域\n"
            "Ctrl+Enter / F5　渲染\n"
            "Ctrl+D　　　　切深浅色\n"
            "Ctrl+1/2/3　　书写 / 样式 / 输出\n"
            "Esc　　　　　取消选中",
            parent=self.root)

    # ------------------------------------------------------------ 状态栏 / 日志
    def _build_statusbar(self, parent):
        ttk = self.ttk
        bar = ttk.Frame(parent, style="Toolbar.TFrame", padding=(16, 4))
        bar.pack(side="bottom", fill="x")
        self.status = ttk.Label(bar, text="就绪", anchor="w",
                                style="Toolbar.Muted.TLabel")
        self.status.pack(side="left", fill="x", expand=True)
        self.status_right = ttk.Label(bar, text="", style="Toolbar.Muted.TLabel")
        self.status_right.pack(side="right")

    def _build_console(self, parent):
        """日志抽屉：平时完全收起，从「⋯」菜单或「输出」页打开。"""
        tk, ttk = self.tk, self.ttk
        wrap = ttk.Frame(parent, style="Toolbar.TFrame", padding=(14, 6, 14, 8))
        self._console_wrap = wrap
        self._console_open = False

        head = ttk.Frame(wrap, style="Toolbar.TFrame")
        head.pack(fill="x")
        ttk.Label(head, text="日志", style="Toolbar.Muted.TLabel").pack(side="left")
        self.console_btn = ttk.Button(head, text="收起",
                                      command=self.toggle_console,
                                      style="Ghost.TButton", width=0)
        self.console_btn.pack(side="right")
        ttk.Button(head, text="清空", command=self._clear_log,
                   style="Ghost.TButton", width=0).pack(side="right", padx=(0, 4))

        body = ttk.Frame(wrap, style="Card.TFrame", padding=(10, 6))
        body.pack(fill="x", pady=(4, 0))
        body.configure(height=int(150 * getattr(self, "_dpi_scale", 1.0)))
        body.pack_propagate(False)
        self.console_body = body
        logrow = ttk.Frame(body, style="Card.TFrame")
        logrow.pack(fill="both", expand=True)
        self.log_text = tk.Text(logrow, height=3, wrap="word", state="disabled",
                                relief="flat", bd=0)
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logrow, orient="vertical",
                           command=self.log_text.yview)
        sb.pack(side="left", fill="y")
        self.log_text.configure(yscrollcommand=sb.set)

        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 0))
        ttk.Label(row, text=">", style="Card.Muted.TLabel").pack(side="left")
        self.entry = ttk.Entry(row)
        self.entry.pack(side="left", fill="x", expand=True, padx=4)
        self.entry.bind("<Return>", lambda e: self._on_command())
        ttk.Button(row, text="执行", command=self._on_command,
                   style="Ghost.TButton", width=0).pack(side="left", padx=1)
        ttk.Button(row, text="记录意见", command=self._record_note,
                   style="Ghost.TButton", width=0).pack(side="left", padx=1)

    def toggle_console(self):
        """日志抽屉：pack 在 paned 之前，收起时整块让位给画布。"""
        if self._console_open:
            try:
                self._console_wrap.pack_forget()
            except Exception:
                pass
            self._console_open = False
        else:
            try:
                self._console_wrap.pack(side="bottom", fill="x",
                                        before=self.paned)
            except Exception:
                try:
                    self._console_wrap.pack(side="bottom", fill="x")
                except Exception:
                    pass
            self._console_open = True
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    def _clear_log(self):
        try:
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    # ------------------------------------------------------------ 卡片 / 分区
    def _section(self, parent, title, open=True):
        """泡沫白卡片 + 可折叠标题；返回内容容器。"""
        ttk = self.ttk
        card = ttk.Frame(parent, style="Card.TFrame", padding=(13, 9))
        card.pack(fill="x", padx=10, pady=(0, 8))
        body = ttk.Frame(card, style="Card.TFrame")
        state = {"open": bool(open)}

        def paint():
            try:
                btn.configure(text=("▾  " if state["open"] else "▸  ") + title)
            except Exception:
                pass

        def toggle():
            state["open"] = not state["open"]
            if state["open"]:
                body.pack(fill="x", pady=(7, 0))
            else:
                body.pack_forget()
            paint()

        btn = ttk.Button(card, text=title, style="Section.TButton", width=0,
                         command=toggle)
        btn.pack(fill="x")
        if state["open"]:
            body.pack(fill="x", pady=(7, 0))
        paint()
        return body

    def _toggle_frames(self):
        self.show_frames = bool(self.var_frames.get())
        self._draw_overlay()

    # ------------------------------------------------------------ 页 1：书写
    def _build_write_panel(self, parent):
        tk, ttk = self.tk, self.ttk
        fam = getattr(self, "_ui_family", "TkDefaultFont")

        body = self._section(parent, "段落")
        self.para_list = _ParagraphList(body, on_select=self._on_para_selected,
                                        rows=4,
                                        dpi_scale=getattr(self, "_dpi_scale", 1.0))
        self.para_list.pack(fill="x")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(8, 0))
        self.draw_btn = None       # 新版：纸面空白处直接拖框，「框选」按钮已去掉
        ttk.Button(row, text="＋ 段落", command=self.add_paragraph,
                   style="Soft.TButton", width=0).pack(side="left")
        ttk.Button(row, text="删除", command=self.delete_paragraph,
                   style="DangerOutline.TButton").pack(side="left", padx=(5, 0))
        ttk.Button(row, text="↓", command=lambda: self.move_paragraph(1),
                   style="Ghost.TButton", width=2).pack(side="right", padx=(1, 0))
        ttk.Button(row, text="↑", command=lambda: self.move_paragraph(-1),
                   style="Ghost.TButton", width=2).pack(side="right")

        body = self._section(parent, "正文")
        self.para_title = ttk.Label(body, text="第 1 段", style="Card.Muted.TLabel")
        self.para_title.pack(anchor="w")
        self.text_widget = tk.Text(body, height=7, wrap="word", relief="flat",
                                   bd=0, highlightthickness=1, undo=True,
                                   font=(fam, 10), padx=8, pady=6)
        self.text_widget.pack(fill="x", pady=(4, 9))
        self.text_widget.bind("<KeyRelease>", self._on_text_key)
        self.text_widget.bind("<FocusOut>",
                              lambda e: self._pull_params(render=True))

        self.var_size = tk.StringVar(value="%.1f" % self.cfg["size"])
        self.var_line = tk.StringVar(value="%.1f" % self.cfg["line"])
        self.var_align = tk.StringVar(value="左对齐")

        grid = ttk.Frame(body, style="Card.TFrame")
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)

        def spin(var, lo, hi, step, col, r, width=6):
            sp = ttk.Spinbox(grid, textvariable=var, from_=lo, to=hi,
                             increment=step, width=width,
                             command=lambda: self._pull_params(render=True))
            sp.grid(row=r, column=col, sticky="ew", padx=(4, 6), pady=2)
            sp.bind("<Return>", lambda e: self._pull_params(render=True))
            sp.bind("<FocusOut>", lambda e: self._pull_params(render=False))
            return sp

        ttk.Label(grid, text="字号", style="Card.Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        spin(self.var_size, 6, 40, 0.2, 1, 0)
        ttk.Label(grid, text="行距", style="Card.Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(6, 0))
        spin(self.var_line, 8, 80, 0.5, 3, 0)
        ttk.Label(grid, text="对齐", style="Card.Muted.TLabel").grid(
            row=1, column=0, sticky="w", pady=(4, 0))
        self.align_box = ttk.Combobox(grid, textvariable=self.var_align,
                                      values=["左对齐", "居中", "右对齐"],
                                      state="readonly", width=8)
        self.align_box.grid(row=1, column=1, columnspan=3, sticky="ew",
                            padx=(4, 6), pady=(4, 0))
        self.align_box.bind("<<ComboboxSelected>>",
                            lambda e: self._pull_params(render=True))

        body = self._section(parent, "背景")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        self.bg_btn = ttk.Button(row, text="空白 A4  ▾",
                                 command=self._show_bg_menu,
                                 style="Soft.TButton", width=0)
        self.bg_btn.pack(side="left")
        self.page_info = ttk.Label(row, text="1/1 页", style="Chip.TLabel")
        self.page_info.pack(side="right")
        self._bg_labels.append(self.bg_btn)

    # ------------------------------------------------------------ 页 2：样式
    def _build_style_panel(self, parent):
        tk, ttk = self.tk, self.ttk

        body = self._section(parent, "字体")
        self.font_var = tk.StringVar(value=_label_for(self.cfg["font"]))
        self.font_box = ttk.Combobox(body, textvariable=self.font_var,
                                     values=[lab for lab, _ in FONT_LABELS],
                                     state="readonly")
        self.font_box.pack(fill="x")
        self.font_box.bind("<<ComboboxSelected>>", lambda e: self._on_font())
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(7, 8))
        ttk.Button(row, text="字库工作台…", command=self.open_fontlab,
                   style="Soft.TButton", width=0).pack(side="left")
        ttk.Button(row, text="补缺字…", command=self.open_fontlab_missing,
                   style="TButton", width=0).pack(side="left", padx=(5, 0))
        ttk.Button(row, text="刷新字库", command=self.reload_fonts,
                   style="TButton", width=0).pack(side="left", padx=(5, 0))
        self.var_variants = tk.BooleanVar(value=bool(self.cfg["variants"]))
        self.var_newsym = tk.BooleanVar(value=bool(self.cfg["newsym"]))
        ttk.Checkbutton(body, text="多套字形轮换", variable=self.var_variants,
                        style="Card.TCheckbutton",
                        command=self._on_render_cfg_change).pack(anchor="w")
        ttk.Checkbutton(body, text="符号用手写库", variable=self.var_newsym,
                        style="Card.TCheckbutton",
                        command=self._on_render_cfg_change).pack(anchor="w")

        body = self._section(parent, "墨色与笔迹")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="墨色", style="Card.Muted.TLabel").pack(side="left")
        self.var_ink = tk.StringVar(value=",".join(str(c) for c in self.cfg["ink"]))
        ent = ttk.Entry(row, textvariable=self.var_ink, width=10)
        ent.pack(side="left", padx=(6, 4))
        ent.bind("<Return>", lambda e: self._on_ink_change())
        ent.bind("<FocusOut>", lambda e: self._on_ink_change())
        ttk.Button(row, text="应用到全部", command=self._ink_all,
                   style="TButton", width=0).pack(side="left")

        grid = ttk.Frame(body, style="Card.TFrame")
        grid.pack(fill="x", pady=(8, 0))
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)
        self.var_jitter = tk.StringVar(value="%.2f" % float(self.cfg["jitter"]))
        self.var_seed = tk.StringVar(value=str(int(self.cfg["seed"])))

        def spin2(var, lo, hi, step, col):
            sp = ttk.Spinbox(grid, textvariable=var, from_=lo, to=hi,
                             increment=step, width=7,
                             command=lambda: self._pull_params(render=True))
            sp.grid(row=0, column=col, sticky="ew", padx=(4, 6), pady=2)
            sp.bind("<Return>", lambda e: self._pull_params(render=True))
            sp.bind("<FocusOut>", lambda e: self._pull_params(render=False))
            return sp

        ttk.Label(grid, text="抖动", style="Card.Muted.TLabel").grid(
            row=0, column=0, sticky="w")
        spin2(self.var_jitter, 0, 2, 0.05, 1)
        ttk.Label(grid, text="种子", style="Card.Muted.TLabel").grid(
            row=0, column=2, sticky="w", padx=(6, 0))
        spin2(self.var_seed, 0, 99999999, 1, 3)

        body = self._section(parent, "高级", open=False)
        self.var_rect = [tk.StringVar(value=v)
                         for v in ("56", "90", "539", "640")]
        ttk.Label(body, text="区域（左 上 右 下）",
                  style="Card.Muted.TLabel").pack(anchor="w")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x", pady=(4, 8))
        for i in range(4):
            e = ttk.Entry(row, textvariable=self.var_rect[i], width=6)
            e.pack(side="left", padx=(0 if i == 0 else 3, 0), expand=True,
                   fill="x")
            e.bind("<Return>", lambda ev: self._pull_params(render=True))
            e.bind("<FocusOut>", lambda ev: self._pull_params(render=False))
        self.var_frames = tk.BooleanVar(value=bool(self.show_frames))
        ttk.Checkbutton(body, text="显示段框", variable=self.var_frames,
                        style="Card.TCheckbutton",
                        command=self._toggle_frames).pack(anchor="w")

    # ------------------------------------------------------------ 页 3：输出
    def _build_out_panel(self, parent):
        tk, ttk = self.tk, self.ttk
        fam = getattr(self, "_ui_family", "TkDefaultFont")

        body = self._section(parent, "导出")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="导出 PDF", command=self.save_pdf,
                   style="Accent.TButton", width=0).pack(
            side="left", expand=True, fill="x")
        ttk.Button(row, text="导出 PNG", command=self.save_png,
                   style="Soft.TButton", width=0).pack(
            side="left", expand=True, fill="x", padx=(6, 0))

        body = self._section(parent, "项目")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="保存项目…", command=self._dlg_save_project,
                   style="Soft.TButton", width=0).pack(
            side="left", expand=True, fill="x")
        ttk.Button(row, text="打开项目…", command=self._dlg_open_project,
                   style="Soft.TButton", width=0).pack(
            side="left", expand=True, fill="x", padx=(6, 0))

        body = self._section(parent, "AI 接口")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        dot = tk.Label(row, text="●", bd=0, bg=self.pal["card"],
                       fg=self.pal["muted"], font=(fam, 9))
        dot.pack(side="left", padx=(0, 6))
        lbl = ttk.Label(row, text=self._ai_status_display,
                        style="Card.Muted.TLabel")
        lbl.pack(side="left")
        self._ai_dots.append(dot)
        self._ai_status_labels.append(lbl)
        ttk.Button(row, text="启动", command=self.launch_ai,
                   style="TButton", width=0).pack(side="right")

        body = self._section(parent, "日志")
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill="x")
        ttk.Button(row, text="查看日志", command=self.toggle_console,
                   style="Soft.TButton", width=0).pack(side="left")
        ttk.Button(row, text="反馈意见", command=self._open_feedback,
                   style="TButton", width=0).pack(side="left", padx=(5, 0))

    # ------------------------------------------------------------ 画布
    def _build_canvas(self, parent):
        tk, ttk = self.tk, self.ttk
        wrap = ttk.Frame(parent, style="Card.TFrame")
        wrap.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(wrap, bg=self.pal["canvas"],
                                highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        # 悬浮缩放工具条（画布右上角，不占顶栏）
        self._float_outer = tk.Frame(self.canvas, bd=0, highlightthickness=1,
                                     bg=self.pal.get("border", "#d5dbe3"))
        self._float_inner = ttk.Frame(self._float_outer,
                                      style="Toolbar.TFrame", padding=(2, 1))
        self._float_inner.pack(padx=1, pady=1)
        fpad = (7, 2)
        ttk.Button(self._float_inner, text="－", command=lambda: self._zoom_step(-1),
                   style="Ghost.TButton", padding=fpad).pack(side="left", padx=1)
        self.zoom_label = ttk.Label(self._float_inner, text="适应",
                                    style="Toolbar.Muted.TLabel")
        self.zoom_label.pack(side="left", padx=3)
        ttk.Button(self._float_inner, text="＋", command=lambda: self._zoom_step(1),
                   style="Ghost.TButton", padding=fpad).pack(side="left", padx=1)
        ttk.Button(self._float_inner, text="适应", command=self.fit_view,
                   style="Ghost.TButton", padding=fpad).pack(side="left", padx=(3, 0))
        self._float_window = self.canvas.create_window(
            0, 0, window=self._float_outer, anchor="ne", tags="floatbar")
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<ButtonPress-2>", self._on_pan_start)
        self.canvas.bind("<B2-Motion>", self._on_pan_motion)
        self.canvas.bind("<ButtonRelease-2>", self._on_pan_end)
        self.canvas.bind("<Double-Button-1>", self._on_double)
        self.canvas.bind("<Motion>", self._on_hover)
        self.canvas.bind("<Leave>", lambda e: self._set_cursor(""))

    def _zoom_step(self, direction):
        """画布悬浮工具条的 －/＋：以画布中心为锚点缩放。"""
        cw, ch = self.canvas_size()
        self._zoom_at(self.view_zoom or self._fit_zoom(cw, ch),
                      cw / 2.0, ch / 2.0, factor=1.2 if direction > 0 else 1 / 1.2)

    # ============================================================ 全局绑定
    def _bind_global(self):
        self.root.bind("<Control-Return>", lambda e: self.render_async())
        self.root.bind("<F5>", lambda e: self.render_async())
        self.root.bind("<Control-d>", lambda e: self.toggle_dark())
        self.root.bind("<KeyPress-space>", self._on_space_down)
        self.root.bind("<KeyRelease-space>", self._on_space_up)
        self.root.bind("<Escape>", lambda e: self._escape())
        for idx, (key, _label) in enumerate(self.PAGES, start=1):
            self.root.bind("<Control-Key-%d>" % idx,
                           lambda e, k=key: self.show_page(k))
        try:
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        except Exception:
            pass

    def _typing_widget_has_focus(self):
        try:
            w = self.root.focus_get()
        except Exception:
            w = None
        if w is None:
            return False
        return isinstance(w, (self.tk.Text, self.tk.Entry,
                              self.ttk.Entry, self.ttk.Combobox,
                              self.ttk.Spinbox))

    def _on_space_down(self, event=None):
        if self._typing_widget_has_focus():
            return
        self._space_down = True
        self._set_cursor("fleur")

    def _on_space_up(self, event=None):
        self._space_down = False
        self._set_cursor("")

    def _escape(self):
        if self.mode == "draw":
            self.set_draw_mode(False)
        self._interact = None
        self._redraw_view()

    # ============================================================ 状态 / 日志
    def log(self, msg):
        try:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", str(msg).rstrip() + "\n")
            self.log_text.see("end")
            self.log_text.configure(state="disabled")
        except Exception:
            pass

    def _current_paragraph(self):
        try:
            if self.selected is not None and 0 <= self.selected < len(self.page.paragraphs):
                return self.page.paragraphs[self.selected]
        except Exception:
            pass
        return None

    def _update_status(self):
        bg = self.page.bg_kind
        if bg == "pdf":
            bg_txt = "PDF %d/%s" % (self.page.bg_page + 1,
                                    self.bg_pdf_pages or "?")
        elif bg == "image":
            bg_txt = "图片"
        else:
            bg_txt = "空白"
        n = len(self.page.paragraphs)
        zoom = "%d%%" % int(round((self.view_zoom or 0) * 100))
        sel = "段 %d/%d" % (self.selected + 1, n) if n else "无段落"
        msg = "%s · %.0f×%.0f · %s · %s" % (
            bg_txt, self.page.width_pt, self.page.height_pt, sel, zoom)
        if self._coverage and "缺" in self._coverage:
            msg += " · " + self._coverage
        if msg != self._last_status:
            self._last_status = msg
            try:
                self.status.configure(text=msg)
            except Exception:
                pass
        try:
            self.zoom_label.configure(text=zoom)
        except Exception:
            pass

    def _update_page_info(self):
        bg = self.page.bg_kind
        if bg == "pdf":
            info = "%d/%s 页" % (self.page.bg_page + 1, self.bg_pdf_pages or "?")
        else:
            info = "1/1 页"
        try:
            self.page_info.configure(text=info)
            self._update_toolbar_dynamic_labels()
            for ent in list(getattr(self, "_page_entries", [])):
                if bg == "pdf":
                    ent.configure(state="normal")
                else:
                    ent.configure(state="disabled")
            self.var_bg_page.set(self.page.bg_page + 1 if bg == "pdf" else 1)
        except Exception:
            pass
        self._update_status()

    # ============================================================ 段落状态同步
    def _refresh_paragraph_list(self):
        try:
            items = []
            for i, p in enumerate(self.page.paragraphs):
                raw = re.sub(r"\s+", " ", (p.text or "").strip())
                title = "%d. %s" % (i + 1, raw or "（空段落）")
                items.append((title, ""))
            self.para_list.set_items(items, selected=self.selected)
        except Exception:
            pass

    def select_paragraph(self, idx, apply_params=True):
        try:
            if idx is None or not (0 <= idx < len(self.page.paragraphs)):
                self.selected = None
                try:
                    self.para_list.select(None)
                except Exception:
                    pass
                return
        except Exception:
            self.selected = None
            return
        self.selected = int(idx)
        try:
            self.para_list.select(self.selected)
        except Exception:
            pass
        if apply_params:
            self._sync_params_from_selected()
        self._draw_overlay()
        self._update_status()

    def _on_para_selected(self, idx):
        if idx is None or idx == self.selected:
            return
        self.select_paragraph(idx, apply_params=True)

    def _sync_params_from_selected(self):
        """段落模型 -> 控件（不触发渲染）。"""
        p = self._current_paragraph()
        self._syncing = True
        try:
            if p is None:
                self.para_title.configure(text="未选中段落")
                self.text_widget.delete("1.0", "end")
                return
            self.para_title.configure(
                text="第 %d 段 · %s" % (
                    self.selected + 1,
                    "空" if not (p.text or "").strip()
                    else "%d 字" % len(p.text)))
            self.text_widget.delete("1.0", "end")
            self.text_widget.insert("1.0", p.text or "")
            self.var_size.set("%.1f" % float(p.size_pt))
            self.var_line.set("%.1f" % float(p.line_pt))
            self.var_jitter.set("%.2f" % float(p.jitter))
            self.var_seed.set(str(int(p.seed)))
            self.var_align.set({"left": "左对齐", "center": "居中",
                                "right": "右对齐"}.get(p.align, "左对齐"))
            for i, v in enumerate(p.rect):
                self.var_rect[i].set(_fmt_pt(float(v)))
            self.var_ink.set(",".join(str(c) for c in p.ink))
        except Exception:
            pass
        finally:
            self._syncing = False

    def _align_from_label(self, label):
        return {"左对齐": "left", "居中": "center",
                "右对齐": "right"}.get(label, "left")

    def _on_align_change(self):
        self._pull_params(render=True)

    def _pull_params(self, render=True):
        """控件 -> 当前段落模型；随后按需延迟渲染。"""
        if self._syncing:
            return
        p = self._current_paragraph()
        if p is None:
            return
        try:
            p.text = self.text_widget.get("1.0", "end").rstrip("\n")
        except Exception:
            pass
        try:
            p.size_pt = max(4.0, min(80.0, float(self.var_size.get())))
        except Exception:
            pass
        try:
            p.line_pt = max(6.0, min(120.0, float(self.var_line.get())))
        except Exception:
            pass
        try:
            p.jitter = max(0.0, min(3.0, float(self.var_jitter.get())))
        except Exception:
            pass
        try:
            p.seed = int(float(self.var_seed.get()))
        except Exception:
            pass
        p.align = self._align_from_label(self.var_align.get())
        try:
            rect = [float(v.get()) for v in self.var_rect]
            if len(rect) == 4:
                p.rect = clamp_rect_to_page(rect, self.page.width_pt,
                                            self.page.height_pt)
        except Exception:
            pass
        try:
            raw = self.var_ink.get().replace("，", ",")
            parts = [int(x) for x in raw.split(",") if x.strip()]
            if len(parts) == 3:
                p.ink = tuple(max(0, min(255, v)) for v in parts)
                self.cfg["ink"] = p.ink
        except Exception:
            pass
        self.cfg["size"] = float(p.size_pt)
        self.cfg["line"] = float(p.line_pt)
        self.cfg["jitter"] = float(p.jitter)
        self.cfg["seed"] = int(p.seed)
        p.normalize()
        self._refresh_paragraph_list()
        self._sync_rect_vars()
        self._draw_overlay()
        if render and self.auto_render:
            self.schedule_render(450)
        self._update_status()

    def _sync_rect_vars(self):
        p = self._current_paragraph()
        if p is None:
            return
        self._syncing = True
        try:
            for i, v in enumerate(p.rect):
                self.var_rect[i].set(_fmt_pt(float(v)))
        finally:
            self._syncing = False

    def _on_text_key(self, _event=None):
        if self._syncing:
            return
        self._cancel("_text_after")
        self._text_after = self._timed(500, lambda: self._pull_params(render=True))
        return None

    # 兼容旧接口名
    def _pull_cfg(self):
        self._pull_params(render=False)

    def _push_cfg(self):
        try:
            self.font_var.set(_label_for(self.cfg["font"]))
            self.var_variants.set(bool(self.cfg["variants"]))
            self.var_newsym.set(bool(self.cfg["newsym"]))
            self.var_ink.set(",".join(str(c) for c in self.cfg["ink"]))
        except Exception:
            pass
        self._sync_params_from_selected()

    def _on_font(self):
        label = self.font_var.get()
        for lab, fn in FONT_LABELS:
            if lab == label:
                self.cfg["font"] = fn
                break
        if (self.cfg["font"] == HAN_OWN or str(self.cfg["font"]).startswith("lib:")) \
                and symbols.available():
            self.var_newsym.set(True)
        if "ZTi/" in str(self.cfg["font"]):
            self.var_variants.set(True)
        self._pull_render_cfg()
        self.log("字体：%s" % self.font_var.get())
        self.schedule_render(80)

    # ------------------------------------------------------------ 字库工作台
    def open_fontlab(self):
        """打开「字库工作台」：自建库 / 识别 / 微调 / 效果示例。"""
        try:
            import fontlab_gui
        except Exception as e:
            self._warn("字库工作台打不开：%r" % (e,))
            return None
        try:
            win = fontlab_gui.open_window(self.root, on_close=self.reload_fonts)
            self.log("已打开字库工作台")
            return win
        except Exception as e:
            self._warn("字库工作台启动失败：%r" % (e,))
            return None

    def open_fontlab_missing(self):
        """把当前正文里缺的字带到字库工作台，直接出补字模板。"""
        text = "\n".join((p.text or "") for p in self.page.paragraphs)
        missing = symbols.missing_han_chars(text)
        if not missing:
            self._warn("当前正文没有缺字（汉字都在字库里）。")
            return None
        self.log("缺字 %d 个：%s" % (len(missing), "".join(missing[:40])))
        try:
            import fontlab_gui
            return fontlab_gui.open_window(
                self.root, on_close=self.reload_fonts,
                prefill_text="".join(missing))
        except Exception as e:
            self._warn("字库工作台启动失败：%r" % (e,))
            return None

    def reload_fonts(self):
        """字库索引变了（新建/启用/删除）后刷新下拉框。"""
        global FONT_LABELS, DEFAULT_FONT_LABEL
        try:
            from fontlib import registry
            registry.load(force=True)
        except Exception:
            pass
        FONT_LABELS = font_labels()
        DEFAULT_FONT_LABEL = _label_for(DEFAULT_FONT)
        try:
            self.font_box["values"] = [lab for lab, _ in FONT_LABELS]
            self.font_var.set(_label_for(self.cfg.get("font")))
        except Exception:
            pass
        self.log("字库列表已刷新（%d 项）" % len(FONT_LABELS))
        self.schedule_render(80)

    def _on_render_cfg_change(self):
        self._pull_render_cfg()
        self.schedule_render(120)

    def _pull_render_cfg(self):
        self.cfg["variants"] = bool(self.var_variants.get())
        self.cfg["newsym"] = bool(self.var_newsym.get())

    def _on_ink_change(self):
        raw = self.var_ink.get().replace("，", ",")
        try:
            parts = [int(x) for x in raw.split(",") if x.strip()]
            if len(parts) != 3:
                self.log("墨色要写成 R,G,B（如 16,16,18）")
                return
        except Exception:
            self.log("墨色要写成 R,G,B（如 16,16,18）")
            return
        ink = tuple(max(0, min(255, v)) for v in parts)
        self.cfg["ink"] = ink
        p = self._current_paragraph()
        if p is not None:
            p.ink = ink
        self._pull_params(render=True)

    def _ink_all(self):
        raw = self.var_ink.get().replace("，", ",")
        try:
            parts = [int(x) for x in raw.split(",") if x.strip()]
            if len(parts) != 3:
                raise ValueError
            ink = tuple(max(0, min(255, v)) for v in parts)
        except Exception:
            self.log("墨色要写 R,G,B")
            return
        self.cfg["ink"] = ink
        for p in self.page.paragraphs:
            p.ink = ink
        self.log("墨色已应用到全部 %d 段" % len(self.page.paragraphs))
        self.schedule_render(80)

    # ============================================================ 段落增删移
    def _default_new_rect(self):
        page = self.page
        if page.paragraphs:
            last = page.paragraphs[-1]
            top = min(last.bottom + 24.0, page.height_pt - 120.0)
        else:
            top = 120.0
        top = max(20.0, top)
        bottom = min(top + 180.0, page.height_pt - 40.0)
        return [56.0, round(top, 1), round(max(120.0, page.width_pt - 56.0), 1),
                round(max(top + 60.0, bottom), 1)]

    def add_paragraph(self, enter_draw=True):
        rect = self._default_new_rect()
        p = self.page.add_paragraph(PM.Paragraph(
            text="", rect=rect, size_pt=self.cfg["size"],
            line_pt=self.cfg["line"], jitter=self.cfg["jitter"],
            seed=self.cfg["seed"], ink=tuple(self.cfg["ink"])))
        self.selected = len(self.page.paragraphs) - 1
        self._refresh_paragraph_list()
        self.select_paragraph(self.selected)
        try:
            self.text_widget.focus_set()
        except Exception:
            pass
        self.log("新增第 %d 段（区域 %.0f,%.0f,%.0f,%.0f pt）"
                 % (self.selected + 1, p.left, p.top, p.right, p.bottom))
        if enter_draw:
            self.set_draw_mode(True)
        self._draw_overlay()
        return p

    def delete_paragraph(self):
        if not self.page.paragraphs:
            return
        idx = self.selected if self.selected is not None else len(self.page.paragraphs) - 1
        if not (0 <= idx < len(self.page.paragraphs)):
            return
        self.page.paragraphs.pop(idx)
        if self.page.paragraphs:
            self.selected = max(0, min(idx, len(self.page.paragraphs) - 1))
        else:
            self.selected = None
        self._refresh_paragraph_list()
        self._sync_params_from_selected()
        self._draw_overlay()
        self.log("已删除段落（剩 %d 段）" % len(self.page.paragraphs))
        self.schedule_render(80)

    def move_paragraph(self, delta):
        if not self.page.paragraphs or self.selected is None:
            return
        i = self.selected
        j = i + int(delta)
        if not (0 <= j < len(self.page.paragraphs)):
            return
        paras = self.page.paragraphs
        paras[i], paras[j] = paras[j], paras[i]
        self.selected = j
        self._refresh_paragraph_list()
        self._sync_params_from_selected()
        self._draw_overlay()
        self.log("段落顺序：%d <-> %d" % (i + 1, j + 1))
        self.schedule_render(80)

    # ============================================================ 背景
    def set_blank_background(self):
        self.page.bg_kind = "blank"
        self.page.bg_path = ""
        self.page.source_pdf = ""
        self.page.bg_page = 0
        self.page.width_pt, self.page.height_pt = PM.A4_W, PM.A4_H
        self.page.invalidate()
        self.bg_pdf_pages = 0
        self.fit_mode = True
        self._update_page_info()
        self._draw_overlay()
        self._redraw_view()
        self.log("背景已清空：空白 A4")
        self.schedule_render(80)

    def load_image_background(self, path):
        """导入图片当整页背景（不弹对话框，方便测试）。"""
        if not path or not os.path.exists(str(path)):
            self._warn("背景图片不存在：%s" % path)
            return False
        path = os.path.abspath(str(path))
        old = (self.page.width_pt, self.page.height_pt)
        self.page.bg_kind = "image"
        self.page.bg_path = path
        self.page.source_pdf = ""
        self.page.bg_page = 0
        self.page.width_pt, self.page.height_pt = PM.A4_W, PM.A4_H
        self.page.invalidate()
        self.bg_pdf_pages = 0
        try:
            self.page.background()        # 立刻解码，早点报错
        except Exception:
            self.page.bg_kind = "blank"
            self.page.bg_path = ""
            self.page.invalidate()
            self._warn("读取背景图片失败：\n" + traceback.format_exc())
            return False
        if old != (self.page.width_pt, self.page.height_pt):
            self._clamp_paragraphs_to_page()
        self.fit_mode = True
        self._update_page_info()
        self._draw_overlay()
        self._redraw_view()
        self.log("已载入图片背景：%s（按比例铺满 A4）" % os.path.basename(path))
        self.schedule_render(80)
        return True

    def load_pdf_background(self, path, page_index=0):
        """把 PDF 的某一页当背景（source_pdf 一并记住，导出 PDF 时保留原内容）。"""
        if not path or not os.path.exists(str(path)):
            self._warn("PDF 不存在：%s" % path)
            return False
        path = os.path.abspath(str(path))
        try:
            import pymupdf as fitz
            doc = fitz.open(path)
            n = len(doc)
            doc.close()
        except Exception:
            self._warn("打开 PDF 失败：\n" + traceback.format_exc())
            return False
        if n <= 0:
            self._warn("PDF 没有页面：%s" % path)
            return False
        idx = max(0, min(int(page_index or 0), n - 1))
        self.page.bg_kind = "pdf"
        self.page.bg_path = path
        self.page.source_pdf = path
        self.page.bg_page = idx
        self.page.bg_dpi = 200
        self.page.width_pt, self.page.height_pt = PM.A4_W, PM.A4_H
        self.page.invalidate()
        try:
            self.page.background()        # 会按 PDF 页尺寸更新 width/height
        except Exception:
            self._warn("PDF 页面渲染失败：\n" + traceback.format_exc())
            return False
        self.bg_pdf_pages = n
        self._clamp_paragraphs_to_page()
        self.fit_mode = True
        self._update_page_info()
        self._draw_overlay()
        self._redraw_view()
        self.log("已导入 PDF 背景：%s 第 %d/%d 页（%.0f×%.0f pt，导出 PDF 会保留原内容）"
                 % (os.path.basename(path), idx + 1, n,
                    self.page.width_pt, self.page.height_pt))
        self.schedule_render(80)
        return True

    def _clamp_paragraphs_to_page(self):
        pw, ph = self.page.width_pt, self.page.height_pt
        for p in self.page.paragraphs:
            p.rect = clamp_rect_to_page(p.rect, pw, ph)
        if self.selected is not None:
            self._sync_rect_vars()

    def clear_background(self):
        if self.page.bg_kind == "blank":
            self.log("背景已经是空白页")
            return
        self.set_blank_background()

    def bg_prev_page(self):
        self._goto_bg_page(self.page.bg_page - 1)

    def bg_next_page(self):
        self._goto_bg_page(self.page.bg_page + 1)

    def _goto_bg_page(self, idx):
        if self.page.bg_kind != "pdf":
            self.log("当前不是 PDF 背景，没有上下页")
            return False
        total = self.bg_pdf_pages or 1
        idx = max(0, min(int(idx), total - 1))
        if idx == self.page.bg_page:
            return False
        return self.load_pdf_background(self.page.bg_path, idx)

    def _on_bg_page_spin(self):
        if self.page.bg_kind != "pdf":
            return
        try:
            idx = int(self.var_bg_page.get()) - 1
        except Exception:
            return
        self._goto_bg_page(idx)

    # ------------------------------------------------------------ 对话框包装
    def _warn(self, msg, title="提示"):
        self.log(msg)
        try:
            if not self.root.winfo_ismapped():
                return
            from tkinter import messagebox
            messagebox.showwarning(title, msg, parent=self.root)
        except Exception:
            pass

    def _dlg_open_image(self):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=self.root, title="选择背景图片",
                filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp *.webp"),
                           ("所有文件", "*.*")])
        except Exception:
            return
        if path:
            self.load_image_background(path)

    def _dlg_import_pdf(self):
        try:
            from tkinter import filedialog, simpledialog
            path = filedialog.askopenfilename(
                parent=self.root, title="选择 PDF",
                filetypes=[("PDF", "*.pdf"), ("所有文件", "*.*")])
            if not path:
                return
            try:
                import pymupdf as fitz
                doc = fitz.open(path)
                n = len(doc)
                doc.close()
            except Exception:
                n = 1
            page = simpledialog.askinteger(
                "PDF 页码", "第几页（1 - %d）？" % n, parent=self.root,
                initialvalue=1, minvalue=1, maxvalue=max(1, n))
        except Exception:
            return
        if path:
            self.load_pdf_background(path, (page or 1) - 1)

    def _dlg_save_project(self):
        try:
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(
                parent=self.root, title="保存项目 JSON",
                defaultextension=".json",
                initialdir=OUT_DIR,
                initialfile="project_%s.json" % _now_stamp(),
                filetypes=[("项目 JSON", "*.json")])
        except Exception:
            return
        if path:
            self.save_project(path)

    def _dlg_open_project(self):
        try:
            from tkinter import filedialog
            path = filedialog.askopenfilename(
                parent=self.root, title="打开项目 JSON",
                initialdir=OUT_DIR,
                filetypes=[("项目 JSON", "*.json"), ("所有文件", "*.*")])
        except Exception:
            return
        if path:
            self.open_project(path)

    # ============================================================ 项目
    def save_project(self, path=None):
        path = path or os.path.join(OUT_DIR, "project_%s.json" % _now_stamp())
        data = {
            "app": "手写体生成器",
            "version": 1,
            "saved": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "spec": PM.page_to_spec(self.page),
            "render": {
                "font": self.cfg.get("font") or DEFAULT_FONT,
                "variants": bool(self.cfg.get("variants")),
                "newsym": bool(self.cfg.get("newsym")),
            },
            "dark": bool(self.dark),
        }
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            with open(path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, ensure_ascii=False, indent=2)
            self.log("项目已保存：%s（%d 段）" % (path, len(self.page.paragraphs)))
            return path
        except Exception:
            self._warn("保存项目失败：\n" + traceback.format_exc())
            return None

    def open_project(self, path):
        try:
            with open(path, encoding="utf-8") as fp:
                data = json.load(fp)
        except Exception:
            self._warn("打开项目失败：\n" + traceback.format_exc())
            return False
        spec = data.get("spec") or data
        try:
            page = PM.page_from_spec(spec)
        except Exception:
            self._warn("项目格式不对：\n" + traceback.format_exc())
            return False
        self.page = page
        rcfg = data.get("render") or {}
        if rcfg.get("font") and any(f == rcfg["font"] for _l, f in FONT_LABELS):
            self.cfg["font"] = rcfg["font"]
        self.cfg["variants"] = bool(rcfg.get("variants"))
        self.cfg["newsym"] = bool(rcfg.get("newsym", self.cfg["newsym"]))
        if data.get("dark") is not None:
            self.dark = bool(data.get("dark"))
        self.selected = 0 if self.page.paragraphs else None
        self.fit_mode = True
        self._push_cfg()
        self._refresh_paragraph_list()
        self._sync_params_from_selected()
        self._update_page_info()
        self._redraw_view()
        self.log("项目已打开：%s（%d 段）" % (path, len(self.page.paragraphs)))
        self.schedule_render(80)
        return True

    # ============================================================ 渲染配置
    def _render_cfg_snapshot(self):
        return {
            "font": self.cfg.get("font") or DEFAULT_FONT,
            "variants": bool(self.cfg.get("variants")),
            "newsym": bool(self.cfg.get("newsym")),
        }

    def _hand_factory(self):
        return make_hand_factory(self._render_cfg_snapshot())

    def _snapshot(self):
        return PM.page_to_spec(self.page)

    def render_async(self):
        """控件快照 -> 子线程渲染 -> 主线程轮询队列取结果（不在子线程碰 tkinter）。"""
        if self.closing:
            return
        self._pull_params(render=False)
        self._pull_render_cfg()
        self.render_token += 1
        token = self.render_token
        spec = self._snapshot()
        rcfg = self._render_cfg_snapshot()
        self._render_active = True

        def worker():
            try:
                page, img, used, warns, note = self._render_job(spec, rcfg)
                self._results.put((token, page, img, used, warns, note, ""))
            except Exception:
                self._results.put((token, None, None, [], [], "",
                                   traceback.format_exc()))
            finally:
                self._render_active = False

        threading.Thread(target=worker, daemon=True, name="gui-render").start()
        self._ensure_render_poll()

    def _ensure_render_poll(self):
        if self.closing or self._poll_after:
            return
        self._poll_after = self._timed(60, self._poll_render_result)

    def _poll_render_result(self):
        """主线程轮询渲染结果（子线程只往 queue.Queue 里放，不碰 tkinter）。"""
        self._poll_after = None
        if self.closing:
            return
        applied = False
        while True:
            try:
                item = self._results.get_nowait()
            except queue.Empty:
                break
            token, page, img, used, warns, note, err = item
            if err:
                self.log("渲染失败：\n" + err)
                continue
            if token != self.render_token:
                continue                      # 已有更新的渲染请求，丢弃旧结果
            self._on_rendered(token, page, img, used, warns, note)
            applied = True
        if self._render_active or not self._results.empty():
            self._ensure_render_poll()

    def schedule_render(self, delay=350):
        if self.closing or not self.auto_render:
            return
        self._cancel("_render_after")
        self._render_after = self._timed(int(delay), self._start_render_thread)

    def _start_render_thread(self):
        self._render_after = None
        if not self.closing:
            self.render_async()

    def _render_job(self, spec, rcfg):
        page = PM.page_from_spec(spec)
        factory = make_hand_factory(rcfg)
        img, used, warns = compose_page(page, factory)
        note = ""
        if rcfg.get("font") == HAN_OWN and symbols.han_available():
            text = "".join((p.get("text") or "")
                           for p in (spec.get("paragraphs") or []))
            note = symbols.han_coverage_summary(text)
        return page, img, used, warns, note

    def render_sync(self):
        """同步渲染（测试 / 导出用）；返回整页 RGB Image。"""
        if self.closing:
            return None
        self._pull_params(render=False)
        self._pull_render_cfg()
        self.render_token += 1
        token = self.render_token
        spec = self._snapshot()
        rcfg = self._render_cfg_snapshot()
        page, img, used, warns, note = self._render_job(spec, rcfg)
        self._on_rendered(token, page, img, used, warns, note, force=True)
        return self.page_image

    def _on_rendered(self, token, page, img, used, warns, note, force=False):
        if self.closing:
            return
        if not force and token != self.render_token:
            return
        self.page.width_pt = float(page.width_pt)
        self.page.height_pt = float(page.height_pt)
        for p, size in zip(self.page.paragraphs, used):
            try:
                p.used_size = (float(size[0]), float(size[1]))
            except Exception:
                pass
        self.page_image = img
        if note:
            self._coverage = note
            if "缺" in note or "缺失" in note:
                self.log(note)
        for w in warns or []:
            self.log("⚠ " + w)
        self._refresh_paragraph_list()
        self._redraw_view()
        self._update_page_info()
        total = len(self.page.paragraphs)
        done = sum(1 for p in self.page.paragraphs if (p.text or "").strip())
        self.log("渲染完成：%d/%d 段有内容 · 页面 %.0f×%.0f pt%s"
                 % (done, total, self.page.width_pt, self.page.height_pt,
                    (" ｜ " + note) if note else ""))

    # ============================================================ 画布变换
    def canvas_size(self):
        if self._canvas_size_override:
            return (max(1, int(self._canvas_size_override[0])),
                    max(1, int(self._canvas_size_override[1])))
        try:
            return (max(1, self.canvas.winfo_width()),
                    max(1, self.canvas.winfo_height()))
        except Exception:
            return (1, 1)

    def _page_px(self):
        """页面图像像素尺寸（有渲染结果用实际图，否则按 SCALE 推算）。"""
        if self.page_image is not None:
            return self.page_image.size
        return (max(1, int(round(self.page.width_pt * H.SCALE))),
                max(1, int(round(self.page.height_pt * H.SCALE))))

    def _pt2screen(self, x_pt, y_pt):
        cw, ch = self.canvas_size()
        iw, ih = self._page_px()
        z = self.view_zoom or self._fit_zoom(cw, ch)
        sx = (cw / 2.0 + self.pan_x) + (x_pt * iw / max(1e-6, self.page.width_pt)
                                        - iw / 2.0) * z
        sy = (ch / 2.0 + self.pan_y) + (y_pt * ih / max(1e-6, self.page.height_pt)
                                        - ih / 2.0) * z
        return sx, sy

    def _screen_to_pt(self, x_screen, y_screen):
        cw, ch = self.canvas_size()
        iw, ih = self._page_px()
        z = max(1e-6, self.view_zoom or self._fit_zoom(cw, ch))
        ix = (x_screen - (cw / 2.0 + self.pan_x)) / z + iw / 2.0
        iy = (y_screen - (ch / 2.0 + self.pan_y)) / z + ih / 2.0
        return (ix * self.page.width_pt / max(1e-6, iw),
                iy * self.page.height_pt / max(1e-6, ih))

    def _margin_px(self):
        """整页 fit 的边距（28 逻辑 px，按 DPI 放大）。"""
        return max(12, int(round(28 * getattr(self, "_dpi_scale", 1.0))))

    def _fit_zoom(self, cw=None, ch=None):
        if cw is None or ch is None:
            cw, ch = self.canvas_size()
        return compute_fit_zoom(cw, ch, self.page.width_pt,
                                self.page.height_pt, margin=self._margin_px())

    def fit_view(self, redraw=True):
        """整页 fit（含 28px 边距）；渲染后不会重置用户的缩放。"""
        cw, ch = self.canvas_size()
        self.view_zoom = self._fit_zoom(cw, ch)
        self.fit_mode = True
        self.pan_x = 0.0
        self.pan_y = 0.0
        if redraw:
            self._redraw_view()
        self.log("适应窗口：整页缩放 %d%%" % int(round(self.view_zoom * 100)))

    # 兼容旧名
    def _redraw(self):
        self._redraw_view()

    def _on_canvas_configure(self, event=None):
        # 保持缩放锚点：pan 是相对画布中心的偏移，窗口尺寸变化时页面中心不动。
        if self.fit_mode:
            cw, ch = ((event.width, event.height) if event is not None
                      else self.canvas_size())
            self.view_zoom = self._fit_zoom(cw, ch)
            self.pan_x = self.pan_y = 0.0
        self._redraw_view()

    def _redraw_view(self):
        if not hasattr(self, "canvas") or self.closing:
            return
        c = self.canvas
        pal = self.pal
        cw, ch = self.canvas_size()
        if self.fit_mode or self.view_zoom <= 0:
            self.view_zoom = self._fit_zoom(cw, ch)
            self.pan_x = self.pan_y = 0.0
        z = max(0.01, float(self.view_zoom))
        cx = cw / 2.0 + self.pan_x
        cy = ch / 2.0 + self.pan_y
        iw, ih = self._page_px()
        disp_w, disp_h = iw * z, ih * z
        x0 = cx - disp_w / 2.0
        y0 = cy - disp_h / 2.0
        x1 = cx + disp_w / 2.0
        y1 = cy + disp_h / 2.0

        try:
            for tag in ("pagebg", "pageimg", "pageframe", "hint", "ov", "ov_draw"):
                c.delete(tag)
            # 柔和阴影（两层偏移矩形模拟半透明）+ 纸面
            off = max(2, int(round(3 * getattr(self, "_dpi_scale", 1.0))))
            canvas_bg = pal.get("canvas", "#dfe4ea")
            shadow = pal.get("shadow", "#b9c1cc")
            c.create_rectangle(x0 + off + 2, y0 + off + 2,
                               x1 + off + 2, y1 + off + 2,
                               fill=_mix(canvas_bg, shadow, 0.18),
                               outline="", tags="pagebg")
            c.create_rectangle(x0 + off, y0 + off, x1 + off, y1 + off,
                               fill=_mix(canvas_bg, shadow, 0.32),
                               outline="", tags="pagebg")
            c.create_rectangle(x0, y0, x1, y1, fill=pal["white"], outline="",
                               tags="pagebg")
            if self.page_image is not None:
                px, py, photo = self._view_photo(cx, cy, cw, ch, z, iw, ih)
                if photo is not None:
                    c.create_image(px, py, anchor="nw", image=photo,
                                   tags="pageimg")
            c.create_rectangle(x0, y0, x1, y1,
                               outline=_mix(pal["border"], pal["text"], 0.15),
                               width=1, tags="pageframe")
            self._draw_overlay()
            self._draw_hint(cw, ch)
            # 悬浮缩放工具条固定在画布右上角
            if self._float_window is not None:
                try:
                    c.coords(self._float_window, cw - 10, 10)
                    c.tag_raise("floatbar")
                except Exception:
                    pass
        except Exception:
            self.log("画布重绘失败：\n" + traceback.format_exc())
        self._update_status()

    def _view_photo(self, cx, cy, cw, ch, z, iw, ih):
        """按可视区域裁剪 + 缩放生成 PhotoImage（高倍缩放也不会爆内存）。

        裁剪区域按 GRID 对齐，平移时通常命中同一块缓存，只需移动图片位置。
        """
        from PIL import ImageTk
        grid = 96
        # 可视源区域（多留一格余量）
        sx0 = (-cx) / z + iw / 2.0 - grid
        sy0 = (-cy) / z + ih / 2.0 - grid
        sx1 = (cw - cx) / z + iw / 2.0 + grid
        sy1 = (ch - cy) / z + ih / 2.0 + grid
        gx0 = max(0, int(math.floor(sx0 / grid)) * grid)
        gy0 = max(0, int(math.floor(sy0 / grid)) * grid)
        gx1 = min(iw, int(math.ceil(sx1 / grid)) * grid)
        gy1 = min(ih, int(math.ceil(sy1 / grid)) * grid)
        if gx1 <= gx0 or gy1 <= gy0:
            return 0.0, 0.0, None
        key = (id(self.page_image), round(z, 5), gx0, gy0, gx1, gy1)
        if key != self._photo_key or self._photo is None:
            crop = self.page_image.crop((gx0, gy0, gx1, gy1))
            tw = max(1, int(round(crop.width * z)))
            th = max(1, int(round(crop.height * z)))
            # 单张位图限制在 6000px 内，防极端缩放
            if tw > 6000 or th > 6000:
                k = min(6000.0 / tw, 6000.0 / th)
                tw, th = max(1, int(tw * k)), max(1, int(th * k))
            self._photo = ImageTk.PhotoImage(crop.resize((tw, th), _RESAMPLE))
            self._photo_key = key
        x = cx + (gx0 - iw / 2.0) * z
        y = cy + (gy0 - ih / 2.0) * z
        return x, y, self._photo

    def _tag_text_font(self):
        if getattr(self, "_tag_font", None) is None:
            try:
                import tkinter.font as tkfont
                self._tag_font = tkfont.Font(
                    family=getattr(self, "_ui_family", "TkDefaultFont"), size=8)
            except Exception:
                self._tag_font = (getattr(self, "_ui_family",
                                          "TkDefaultFont"), 8)
        return self._tag_font

    def _draw_number_capsule(self, x, y, num, selected):
        """段号小胶囊：选中=主色实心，未选中=白底主色描边（白纸上更清晰）。"""
        pal = self.pal
        f = self._tag_text_font()
        txt = str(num)
        try:
            tw = f.measure(txt)
            lh = f.metrics("linespace")
        except Exception:
            tw, lh = 8 * len(txt), 16
        h = lh + 5
        w = tw + 12
        r = h / 2.0
        if selected:
            fill = pal["accent"]
            outline = pal["accent"]
            fg = pal.get("accent_text", "#ffffff")
        else:
            fill = pal.get("white", "#ffffff")
            outline = pal["accent"]
            fg = pal["accent"]
        c = self.canvas
        c.create_oval(x, y, x + h, y + h, fill=fill, outline=outline,
                      width=1, tags="ov")
        c.create_oval(x + w - h, y, x + w, y + h, fill=fill, outline=outline,
                      width=1, tags="ov")
        c.create_rectangle(x + r, y, x + w - r, y + h, fill=fill, outline="",
                           tags="ov")
        c.create_line(x + r, y, x + w - r, y, fill=outline, tags="ov")
        c.create_line(x + r, y + h, x + w - r, y + h, fill=outline, tags="ov")
        c.create_text(x + w / 2.0, y + h / 2.0, text=txt, fill=fg, font=f,
                      tags="ov")

    def _draw_overlay(self):
        if not hasattr(self, "canvas") or self.closing:
            return
        c = self.canvas
        try:
            c.delete("ov")
        except Exception:
            return
        if not self.show_frames:
            return
        pal = self.pal
        try:
            for i, p in enumerate(self.page.paragraphs):
                x0, y0 = self._pt2screen(p.left, p.top)
                x1, y1 = self._pt2screen(p.right, p.bottom)
                selected = (i == self.selected)
                if selected:
                    color = pal["accent"]
                    dash = None
                    width = 2
                else:
                    color = _mix(pal["text"], pal["white"], 0.55)
                    dash = (4, 3)
                    width = 1
                c.create_rectangle(x0, y0, x1, y1, outline=color,
                                   width=width, dash=dash, tags="ov")
                # 段号胶囊放在框外左上角，避免盖住正文起笔
                cap_font = self._tag_text_font()
                try:
                    cap_h = cap_font.metrics("linespace") + 5
                except Exception:
                    cap_h = 18
                cap_y = y0 - cap_h - 3
                if cap_y < 2:
                    cap_y = y0 + 3
                self._draw_number_capsule(x0, cap_y, i + 1, selected)
                if selected:
                    self._draw_handles(x0, y0, x1, y1)
        except Exception:
            pass

    def _handle_points(self, x0, y0, x1, y1):
        xm, ym = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        return {
            "nw": (x0, y0), "n": (xm, y0), "ne": (x1, y0),
            "e": (x1, ym), "se": (x1, y1), "s": (xm, y1),
            "sw": (x0, y1), "w": (x0, ym),
        }

    def _draw_handles(self, x0, y0, x1, y1):
        c = self.canvas
        pal = self.pal
        for _name, (hx, hy) in self._handle_points(x0, y0, x1, y1).items():
            c.create_rectangle(hx - 4, hy - 4, hx + 4, hy + 4,
                               fill=pal["accent"], outline=pal["white"],
                               width=1, tags="ov")

    def _draw_hint(self, cw, ch):
        """只在拉框时给一行提示；平时画布上不放说明文字。"""
        if self.mode != "draw":
            return
        pal = self.pal
        try:
            self.canvas.create_text(16, ch - 12, anchor="sw",
                                    text="拖出矩形 · Esc 取消",
                                    fill=pal["accent"],
                                    font=(self._ui_family, 9), tags="hint")
        except Exception:
            pass

    def _set_cursor(self, cursor):
        if cursor == self._hover_cursor:
            return
        self._hover_cursor = cursor
        try:
            self.canvas.configure(cursor=cursor)
        except Exception:
            pass

    # ============================================================ 画布交互
    def _hit_test(self, x, y):
        """返回 ('handle', idx, name) / ('rect', idx, None) / None。"""
        if not self.page.paragraphs:
            return None
        # 先测选中段的手柄
        if self.selected is not None and 0 <= self.selected < len(self.page.paragraphs):
            p = self.page.paragraphs[self.selected]
            x0, y0 = self._pt2screen(p.left, p.top)
            x1, y1 = self._pt2screen(p.right, p.bottom)
            for name, (hx, hy) in self._handle_points(x0, y0, x1, y1).items():
                if abs(x - hx) <= 6 and abs(y - hy) <= 6:
                    return ("handle", self.selected, name)
        # 再从后往前测矩形
        for i in range(len(self.page.paragraphs) - 1, -1, -1):
            p = self.page.paragraphs[i]
            x0, y0 = self._pt2screen(p.left, p.top)
            x1, y1 = self._pt2screen(p.right, p.bottom)
            if min(x0, x1) <= x <= max(x0, x1) and min(y0, y1) <= y <= max(y0, y1):
                return ("rect", i, None)
        return None

    def _zoom_at(self, z_old, sx, sy, factor):
        """以画布坐标 (sx,sy) 为锚点缩放：缩放前后光标下的页面点不动。"""
        cw, ch = self.canvas_size()
        z_old = float(z_old or self._fit_zoom(cw, ch))
        z_new = max(0.02, min(6.0, z_old * float(factor)))
        if abs(z_new - z_old) < 1e-6:
            return
        px, py = self._screen_to_pt(sx, sy)
        iw, ih = self._page_px()
        self.view_zoom = z_new
        self.fit_mode = False
        self.pan_x = sx - cw / 2.0 - (px * iw / max(1e-6, self.page.width_pt)
                                      - iw / 2.0) * z_new
        self.pan_y = sy - ch / 2.0 - (py * ih / max(1e-6, self.page.height_pt)
                                      - ih / 2.0) * z_new
        self._redraw_view()

    def _on_wheel(self, event):
        if not self.page.paragraphs:
            return
        cw, ch = self.canvas_size()
        z_old = self.view_zoom or self._fit_zoom(cw, ch)
        factor = 1.12 if event.delta > 0 else 1 / 1.12
        self._zoom_at(z_old, event.x, event.y, factor)

    def _on_double(self, _event=None):
        self.fit_view()

    def _on_hover(self, event):
        if self._interact is not None:
            return
        if self.mode == "draw":
            self._set_cursor("crosshair")
            return
        hit = self._hit_test(event.x, event.y)
        if not hit:
            # 纸面空白 = 可拖框；纸外 = 可平移
            px, py = self._screen_to_pt(event.x, event.y)
            on_paper = (0 <= px <= self.page.width_pt
                        and 0 <= py <= self.page.height_pt)
            self._set_cursor("crosshair" if on_paper else "fleur")
            return
        kind = hit[0]
        if kind == "handle":
            name = hit[2] or ""
            cursor = {"nw": "size_nw_se", "se": "size_nw_se",
                      "ne": "size_ne_sw", "sw": "size_ne_sw",
                      "n": "size_ns", "s": "size_ns",
                      "e": "size_we", "w": "size_we"}.get(name, "sizing")
            self._set_cursor(cursor)
        else:
            self._set_cursor("fleur")

    def _on_press(self, event):
        if self.mode == "draw":
            self._begin_draw(event.x, event.y)
            return
        if self._space_down:
            self._begin_pan(event.x, event.y)
            return
        hit = self._hit_test(event.x, event.y)
        if hit:
            kind = hit[0]
            if kind == "handle":
                self._begin_resize(hit[1], hit[2], event.x, event.y)
                return
            if kind == "rect":
                if hit[1] != self.selected:
                    self.select_paragraph(hit[1])
                self._begin_move(hit[1], event.x, event.y)
                return
        # 纸面空白处按住拖动 = 重设当前段区域（不再需要单独的「框选」按钮）
        px, py = self._screen_to_pt(event.x, event.y)
        if 0 <= px <= self.page.width_pt and 0 <= py <= self.page.height_pt:
            self._begin_draw(event.x, event.y)
            return
        self._begin_pan(event.x, event.y)

    def _on_motion(self, event):
        it = self._interact
        if not it:
            return
        kind = it.get("kind")
        if kind == "pan":
            self.pan_x = it["pan_x"] + (event.x - it["x"])
            self.pan_y = it["pan_y"] + (event.y - it["y"])
            self._redraw_view()
        elif kind == "move":
            px, py = self._screen_to_pt(event.x, event.y)
            dx, dy = px - it["start_pt"][0], py - it["start_pt"][1]
            p = self.page.paragraphs[it["idx"]]
            p.rect = move_rect(it["orig"], dx, dy, self.page.width_pt,
                               self.page.height_pt)
            self._sync_rect_vars()
            self._draw_overlay()
        elif kind == "resize":
            px, py = self._screen_to_pt(event.x, event.y)
            dx, dy = px - it["start_pt"][0], py - it["start_pt"][1]
            p = self.page.paragraphs[it["idx"]]
            p.rect = resize_rect(it["orig"], it["handle"], dx, dy,
                                 self.page.width_pt, self.page.height_pt)
            self._sync_rect_vars()
            self._draw_overlay()
        elif kind == "draw":
            self._update_draw(event.x, event.y)

    def _on_release(self, event):
        it = self._interact
        if not it:
            self._interact = None
            return
        kind = it.get("kind")
        if kind == "draw":
            # 注意：先别清 _interact，_end_draw 还要用它取起点
            self._end_draw(event.x, event.y)
            return
        self._interact = None
        if kind in ("move", "resize"):
            self._refresh_paragraph_list()
            self._draw_overlay()
            self._update_status()
            if self.auto_render:
                self.schedule_render(220)

    def _begin_pan(self, x, y):
        self._interact = {"kind": "pan", "x": x, "y": y,
                          "pan_x": self.pan_x, "pan_y": self.pan_y}
        self._set_cursor("fleur")

    def _on_pan_start(self, event):
        self._begin_pan(event.x, event.y)

    def _on_pan_motion(self, event):
        self._on_motion(event)

    def _on_pan_end(self, _event=None):
        self._interact = None
        self._set_cursor("")

    def _begin_move(self, idx, x, y):
        p = self.page.paragraphs[idx]
        self._interact = {"kind": "move", "idx": idx, "orig": list(p.rect),
                          "start_pt": self._screen_to_pt(x, y)}
        self._set_cursor("fleur")

    def _begin_resize(self, idx, handle, x, y):
        p = self.page.paragraphs[idx]
        self._interact = {"kind": "resize", "idx": idx, "handle": handle,
                          "orig": list(p.rect),
                          "start_pt": self._screen_to_pt(x, y)}
        self._on_hover_pos(x, y)

    def _on_hover_pos(self, x, y):
        name = (self._interact or {}).get("handle", "")
        cursor = {"nw": "size_nw_se", "se": "size_nw_se",
                  "ne": "size_ne_sw", "sw": "size_ne_sw",
                  "n": "size_ns", "s": "size_ns",
                  "e": "size_we", "w": "size_we"}.get(name, "sizing")
        self._set_cursor(cursor)

    # ---- 拉框
    def set_draw_mode(self, on):
        self.mode = "draw" if on else "select"
        self._set_cursor("crosshair" if on else "")
        self._redraw_view()
        if on:
            self.log("拉框模式：在画布上按住左键拖出矩形（Esc 取消）")

    def toggle_draw_mode(self):
        self.set_draw_mode(self.mode != "draw")

    def _begin_draw(self, x, y):
        self._interact = {"kind": "draw", "x0": x, "y0": y}
        self._set_cursor("crosshair")

    def _update_draw(self, x, y):
        it = self._interact or {}
        x0, y0 = it.get("x0"), it.get("y0")
        if x0 is None:
            return
        try:
            self.canvas.delete("ov_draw")
            self.canvas.create_rectangle(x0, y0, x, y, outline=self.pal["accent"],
                                         width=2, dash=(6, 3), tags="ov_draw")
        except Exception:
            pass

    def _end_draw(self, x, y):
        it = self._interact or {}
        self._interact = None
        x0, y0 = it.get("x0"), it.get("y0")
        try:
            self.canvas.delete("ov_draw")
        except Exception:
            pass
        if x0 is None:
            return
        a = self._screen_to_pt(x0, y0)
        b = self._screen_to_pt(x, y)
        left, right = sorted((a[0], b[0]))
        top, bottom = sorted((a[1], b[1]))
        if right - left < 20 or bottom - top < 14:
            # 单击空白处（没拖开）不当作拉框，静默忽略
            self.set_draw_mode(False)
            return
        p = self._current_paragraph()
        if p is None:
            p = self.page.add_paragraph(PM.Paragraph(
                text="", rect=[left, top, right, bottom],
                size_pt=self.cfg["size"], line_pt=self.cfg["line"],
                jitter=self.cfg["jitter"], seed=self.cfg["seed"],
                ink=tuple(self.cfg["ink"])))
            self.selected = len(self.page.paragraphs) - 1
        p.rect = clamp_rect_to_page([left, top, right, bottom],
                                    self.page.width_pt, self.page.height_pt)
        self._refresh_paragraph_list()
        self._sync_params_from_selected()
        self.set_draw_mode(False)
        self._draw_overlay()
        self.log("第 %d 段区域：左 %.1f 上 %.1f 右 %.1f 下 %.1f pt（%.0f×%.0f）"
                 % (self.selected + 1, p.left, p.top, p.right, p.bottom,
                    p.width, p.height))
        if self.auto_render:
            self.schedule_render(200)

    # ============================================================ 导出
    def _ensure_out(self):
        os.makedirs(OUT_DIR, exist_ok=True)
        return OUT_DIR

    def save_png(self, path=None):
        if self.page_image is None and not self.page.paragraphs:
            self.log("还没有内容可导出")
            return None
        try:
            img = self.render_sync()
        except Exception:
            self._warn("导出前渲染失败：\n" + traceback.format_exc())
            return None
        if img is None:
            return None
        if not path:
            self._ensure_out()
            path = os.path.join(OUT_DIR, "gui_%s.png" % _now_stamp())
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            out = Image.new("RGB", img.size, (255, 255, 255))
            out.paste(img.convert("RGB"), (0, 0))
            out.save(path)
            self.log("已导出 PNG：%s（%dx%d）" % (path, out.width, out.height))
            return path
        except Exception:
            self._warn("导出 PNG 失败：\n" + traceback.format_exc())
            return None

    def save_pdf(self, path=None):
        if not path:
            self._ensure_out()
            path = os.path.join(OUT_DIR, "gui_%s.pdf" % _now_stamp())
        try:
            # 先按当前参数渲染一遍，保证和预览一致
            self.render_sync()
            if self.cfg.get("font") == HAN_OWN and symbols.han_available():
                # 默认字体：直接用共享核心的导出（源 PDF 原内容保留 + 手写层叠加）
                PM.export_pdf(self.page, path)
            else:
                export_pdf_with_factory(self.page, path, self._hand_factory())
            self.log("已导出 PDF：%s（%s）" % (
                path, "在源 PDF 上叠加手写层，原内容保留"
                if self.page.source_pdf else "新建页面（背景 + 手写层）"))
            return path
        except Exception:
            self._warn("导出 PDF 失败：\n" + traceback.format_exc())
            return None

    # ============================================================ AI 接口
    def _set_ai_status(self, text):
        self._ai_status_text = text
        display = str(text or "")
        if display.startswith("AI："):
            display = display[3:]
        self._ai_status_display = display
        low = str(text or "")
        if "就绪" in low:
            dot = self.pal.get("accent", "#4f6bed")
        elif ("失败" in low or "退出" in low or "缺失" in low
              or "未响应" in low):
            dot = self.pal.get("danger", "#e5484d")
        else:
            dot = self.pal.get("muted", "#6b7280")
        for w in list(getattr(self, "_ai_dots", [])):
            try:
                w.configure(fg=dot, bg=self.pal.get("card", "#FFFCF4"))
            except Exception:
                pass
        for w in list(getattr(self, "_ai_status_labels", [])):
            try:
                w.configure(text=display)
            except Exception:
                pass
        self._update_toolbar_dynamic_labels()

    def _detect_ai_port(self, script):
        default = AI_DEFAULT_PORT
        if getattr(sys, "frozen", False):
            return default
        try:
            with open(script, encoding="utf-8", errors="ignore") as fp:
                src = fp.read()
            for pat in (r"(?:DEFAULT_)?PORT\s*=\s*(\d{2,5})",
                        r"AI_PORT\s*=\s*(\d{2,5})",
                        r"HTTP_PORT\s*=\s*(\d{2,5})"):
                m = re.search(pat, src)
                if m:
                    return int(m.group(1))
            m = re.search(r"127\.0\.0\.1:(\d{2,5})", src)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        return default

    def launch_ai(self, script_path=None):
        """用 `py -3 ai_api.py` 启动 AI 接口；显示端口状态，失败给提示。"""
        if self.ai_proc is not None and self.ai_proc.poll() is None:
            self.log("AI 接口已在运行（pid %s，端口 %d）"
                     % (self.ai_proc.pid, self.ai_port))
            return True
        script = script_path or os.path.join(HERE, "ai_api.py")
        self._set_ai_status("AI：启动中…")
        if not os.path.exists(script):
            msg = ("AI 接口未就绪：找不到 %s\n（等 ai-service 交付 ai_api.py 后再点）"
                   % script)
            self.log(msg)
            self._set_ai_status("AI：脚本缺失")
            self._warn(msg, "AI 接口未就绪")
            return False
        port = self._detect_ai_port(script)
        self.ai_port = port
        try:
            self._ensure_out()
            log_path = os.path.join(OUT_DIR, "ai_api.log")
            if self._ai_logf:
                try:
                    self._ai_logf.close()
                except Exception:
                    pass
            self._ai_logf = open(log_path, "ab")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            if getattr(sys, "frozen", False):
                # 打包版：同目录下单独的 AI 接口 exe
                _base = os.path.dirname(sys.executable)
                ai_exe = os.path.join(_base, "ZTiAI接口.exe")
                cmd = [ai_exe] if os.path.exists(ai_exe) else [sys.executable,
                                                               script]
            else:
                cmd = ["py", "-3", script]
            try:
                self.ai_proc = subprocess.Popen(
                    cmd, cwd=HERE, stdout=self._ai_logf,
                    stderr=subprocess.STDOUT, creationflags=flags)
            except Exception:
                self.ai_proc = subprocess.Popen(
                    [sys.executable, script], cwd=HERE, stdout=self._ai_logf,
                    stderr=subprocess.STDOUT, creationflags=flags)
        except Exception:
            self._set_ai_status("AI：启动失败")
            self._warn("启动 AI 接口失败：\n" + traceback.format_exc())
            return False
        self.log("AI 接口已启动：py -3 ai_api.py（pid %s，探测端口 %d，日志 out/ai_api.log）"
                 % (self.ai_proc.pid, port))
        proc = self.ai_proc
        self._ai_poll_left = 40
        threading.Thread(target=self._ai_probe, args=(proc, port),
                         daemon=True, name="ai-probe").start()
        self._timed(250, self._poll_ai_result)
        return True

    def _ai_probe(self, proc, port):
        """子线程只探测端口，把结果放进队列；tkinter 由主线程轮询处理。"""
        import socket
        import time
        ok = False
        for _ in range(30):
            if proc.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.4):
                    ok = True
                    break
            except OSError:
                time.sleep(0.2)
        self._ai_queue.put((proc, port, ok, proc.poll()))

    def _poll_ai_result(self):
        if self.closing:
            return
        try:
            proc, port, ok, code = self._ai_queue.get_nowait()
        except queue.Empty:
            self._ai_poll_left -= 1
            if self._ai_poll_left > 0:
                self._timed(250, self._poll_ai_result)
            return
        self._ai_probe_done(proc, port, ok, code)

    def _ai_probe_done(self, proc, port, ok, code):
        if ok:
            self._set_ai_status("AI：端口 %d 就绪" % port)
            self.log("AI 接口端口 %d 已可访问（pid %s）" % (port, proc.pid))
        elif code is not None:
            self._set_ai_status("AI：进程退出 %s" % code)
            self._warn("AI 接口进程已退出（code %s），详见 out/ai_api.log"
                       % code, "AI 接口启动失败")
        else:
            self._set_ai_status("AI：端口 %d 未响应" % port)
            self.log("AI 接口进程在跑（pid %s）但端口 %d 还没响应，"
                     "可能还在启动或端口不同，详见 out/ai_api.log"
                     % (proc.pid, port))

    # ============================================================ 恢复控件状态
    def _restore_ui_state(self):
        self._push_cfg()
        self._refresh_paragraph_list()
        self._sync_params_from_selected()
        self._update_page_info()
        self._redraw_view()

    # ============================================================ 控制台
    def _on_command(self):
        raw = self.entry.get().strip()
        if not raw:
            return
        self.entry.delete(0, "end")
        self.log("> " + raw)
        if self._try_command(raw):
            return
        self._record_feedback(raw)

    def _record_note(self):
        text = self.entry.get().strip()
        if not text:
            self.log("意见为空")
            return
        self.entry.delete(0, "end")
        self.log("> " + text)
        self._record_feedback(text)

    def _open_feedback(self):
        import subprocess as sp
        try:
            if not os.path.exists(FEEDBACK):
                self._record_feedback("（初始化）")
            os.startfile(FEEDBACK)  # Windows
        except Exception:
            try:
                sp.Popen(["notepad.exe", FEEDBACK])
            except Exception:
                self.log("打不开 feedback.md，路径：%s" % FEEDBACK)

    def _record_feedback(self, text, path=None):
        path = path or FEEDBACK
        try:
            folder = os.path.dirname(os.path.abspath(path))
            if folder:
                os.makedirs(folder, exist_ok=True)
            new = not os.path.exists(path)
            with open(path, "a", encoding="utf-8") as fp:
                if new:
                    fp.write("# PDF-Layer 修改意见（控制台记录）\n\n")
                fp.write("- `%s`  %s\n"
                         % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
            self.log("已记录到 feedback.md")
        except Exception:
            self.log("写 feedback.md 失败：\n" + traceback.format_exc())

    def _try_command(self, raw):
        low = raw.strip().lower()
        if low in ("help", "?", "？"):
            self.log(
                "命令：\n"
                "  参数  size=13.2  line=24  jitter=0.8  seed=1  align=left|center|right\n"
                "        ink=16,16,18  rect=56,120,539,400  width=404\n"
                "        font=名字  variants=on|off  newsym=on|off  page=on|off\n"
                "  动作  render | save png [路径] | save pdf [路径] | fit | zoom=0.5|fit\n"
                "        new | del | up | down | next | prev\n"
                "        bg image=路径 | bg pdf=路径 [page=N] | bg clear\n"
                "        dark=on|off | project save [路径] | project open 路径 | ai start\n"
                "  其他文字会追加到 feedback.md")
            return True
        if low in ("render", "r", "draw"):
            self.render_async()
            return True
        if low.startswith("save png"):
            path = raw[8:].strip().strip('"') or None
            self.save_png(path)
            return True
        if low.startswith("save pdf"):
            path = raw[8:].strip().strip('"') or None
            self.save_pdf(path)
            return True
        if low in ("fit", "适应", "fit window"):
            self.fit_view()
            return True
        if low in ("new", "add", "新段"):
            self.add_paragraph()
            return True
        if low in ("del", "delete", "remove", "删除"):
            self.delete_paragraph()
            return True
        if low in ("up", "上移"):
            self.move_paragraph(-1)
            return True
        if low in ("down", "下移"):
            self.move_paragraph(1)
            return True
        if low in ("next", "下一页"):
            self.bg_next_page()
            return True
        if low in ("prev", "上一页"):
            self.bg_prev_page()
            return True
        if low in ("ai start", "ai"):
            self.launch_ai()
            return True
        if low == "clear":
            try:
                self.log_text.configure(state="normal")
                self.log_text.delete("1.0", "end")
                self.log_text.configure(state="disabled")
            except Exception:
                pass
            return True
        if low.startswith("zoom="):
            val = low[5:].strip()
            if val in ("fit", "auto", "0"):
                self.fit_view()
            else:
                try:
                    z = max(0.02, min(6.0, float(val)))
                except Exception:
                    self.log("zoom= 后面要写数字或 fit")
                    return True
                self.fit_mode = False
                self.pan_x = self.pan_y = 0.0
                self.view_zoom = z
                self._redraw_view()
            return True
        if low.startswith("project "):
            rest = raw[len("project "):].strip()
            rlow = rest.lower()
            if rlow.startswith("save"):
                path = rest[4:].strip().strip('"') or None
                self.save_project(path)
                return True
            if rlow.startswith("open"):
                path = rest[4:].strip().strip('"')
                if not path:
                    self.log("project open 后面要写 JSON 路径")
                else:
                    self.open_project(path)
                return True
            return False
        if low.startswith("bg "):
            rest = raw[3:].strip()
            m = re.match(r"(?i)^image\s*=\s*(.+)$", rest)
            if m:
                self.load_image_background(m.group(1).strip().strip('"'))
                return True
            m = re.match(r"(?i)^pdf\s*=\s*(.+)$", rest)
            if m:
                val = m.group(1).strip()
                pm_ = re.search(r"\s+page\s*=\s*(\d+)\s*$", val, re.I)
                idx = 1
                if pm_:
                    idx = int(pm_.group(1))
                    val = val[:pm_.start()].strip()
                self.load_pdf_background(val.strip('"'), idx - 1)
                return True
            if rest.lower() in ("clear", "blank", "off"):
                self.clear_background()
                return True
            self.log("bg 用法：bg image=路径 / bg pdf=路径 [page=N] / bg clear")
            return True
        if low.startswith("dark="):
            on = low[5:].strip() in ("on", "1", "true", "yes", "开", "深色")
            self.set_dark(on)
            return True

        m = re.match(r"^([a-zA-Z_]+)\s*=\s*(.+)$", raw)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            try:
                p_sel = self._current_paragraph()
                if key == "size":
                    self.cfg["size"] = max(4.0, float(val))
                    if p_sel is not None:
                        p_sel.size_pt = self.cfg["size"]
                elif key == "line":
                    self.cfg["line"] = max(6.0, float(val))
                    if p_sel is not None:
                        p_sel.line_pt = self.cfg["line"]
                elif key == "jitter":
                    self.cfg["jitter"] = max(0.0, min(3.0, float(val)))
                    if p_sel is not None:
                        p_sel.jitter = self.cfg["jitter"]
                elif key == "seed":
                    self.cfg["seed"] = int(val)
                    if p_sel is not None:
                        p_sel.seed = self.cfg["seed"]
                elif key == "width":
                    p = self._current_paragraph()
                    if p is not None:
                        p.rect = [p.left, p.top, min(self.page.width_pt,
                                                     p.left + float(val)),
                                  p.bottom]
                elif key == "ink":
                    parts = [int(x) for x in val.replace("，", ",").split(",")
                             if x.strip()]
                    if len(parts) != 3:
                        raise ValueError("墨色要写成 R,G,B")
                    self.cfg["ink"] = tuple(max(0, min(255, v)) for v in parts)
                    p = self._current_paragraph()
                    if p is not None:
                        p.ink = self.cfg["ink"]
                elif key == "rect":
                    parts = [float(x) for x in val.replace("，", ",").split(",")
                             if x.strip()]
                    if len(parts) != 4:
                        raise ValueError("rect 要写成 l,t,r,b")
                    p = self._current_paragraph()
                    if p is not None:
                        p.rect = clamp_rect_to_page(parts, self.page.width_pt,
                                                    self.page.height_pt)
                elif key == "align":
                    mapping = {"left": "left", "center": "center",
                               "right": "right", "左": "left", "中": "center",
                               "右": "right"}
                    if val.lower() not in mapping:
                        raise ValueError("align 取 left/center/right")
                    p = self._current_paragraph()
                    if p is not None:
                        p.align = mapping[val.lower()]
                elif key == "variants":
                    self.cfg["variants"] = val.lower() in ("on", "1", "true",
                                                           "yes", "开")
                elif key == "newsym":
                    self.cfg["newsym"] = val.lower() in ("on", "1", "true",
                                                         "yes", "开")
                elif key == "page":
                    on = val.lower() in ("on", "1", "true", "yes", "开")
                    if on:
                        self.fit_view()
                elif key == "font":
                    hit = [fn for lab, fn in FONT_LABELS
                           if val in lab or val == fn]
                    if not hit:
                        raise ValueError("未知字体，可选：%s"
                                         % "、".join(l for l, _ in FONT_LABELS))
                    self.cfg["font"] = hit[0]
                elif key in ("zoom",):
                    self.fit_mode = False
                    self.view_zoom = max(0.02, min(6.0, float(val)))
                    self._redraw_view()
                else:
                    return False
                self._push_cfg()
                self.log("已设置 %s = %s" % (key, val))
                if key == "page":
                    return True
                self._pull_params(render=False)
                self.schedule_render(100)
                return True
            except Exception as exc:
                self.log("参数错误：%s" % exc)
                return True
        return False


# ================================================================ 入口
def _set_window_icon_gui(root):
    """手写工具窗口图标（工作室 Z-W 标）。"""
    bases = [getattr(sys, "_MEIPASS", ""), HERE]
    for b in bases:
        if not b:
            continue
        for name in ("app_icon_256.png", "app_icon_64.png"):
            p = os.path.join(b, "assets", "icon", name)
            if os.path.exists(p):
                try:
                    img = _tk.PhotoImage(file=p)
                    root.iconphoto(True, img)
                    root._zhi_icon = img
                    return True
                except Exception:
                    pass
    return False


def _smoke_report(ok, text=""):
    """打包后没有控制台：把自检结果写到 exe 同目录的 smoke_result.txt。"""
    try:
        base = (os.path.dirname(sys.executable)
                if getattr(sys, "frozen", False) else HERE)
        with open(os.path.join(base, "smoke_result.txt"), "w",
                  encoding="utf-8") as f:
            f.write(("SMOKE OK\n" if ok else "SMOKE FAIL\n") + text)
    except Exception:
        pass
    print(("SMOKE OK\n" if ok else "SMOKE FAIL\n") + text)


def _smoke():
    """无窗口自检：建 App、画一页测试文本、销毁。"""
    if _tk is None:
        _smoke_report(False, "没有 tkinter")
        return 1
    root = _tk.Tk()
    root.withdraw()
    try:
        app = App(root)
        root.update_idletasks()
        root.update()
    except Exception:
        _smoke_report(False, traceback.format_exc())
        root.destroy()
        return 1
    finally:
        pass
    try:
        root.destroy()
    except Exception:
        pass
    _smoke_report(True, "App 构建/刷新正常，字体=%s" % DEFAULT_FONT)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--smoke" in argv:
        return _smoke()
    enable_dpi_awareness()
    if _tk is None:
        print("没有 tkinter，无法启动图形界面。")
        return 1
    root = _tk.Tk()
    _set_window_icon_gui(root)
    try:
        App(root)
    except Exception:
        print(traceback.format_exc())
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
