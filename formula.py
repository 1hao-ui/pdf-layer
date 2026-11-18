# -*- coding: utf-8 -*-
"""formula.py — 公式层：把 LaTeX 子集渲染成与正文同风格的手写位图。

为什么这么做
------------
手写字体（马善政楷书等）没有 OpenType MATH 表，因此分式、根号、大运算符、
自适应括号这些「二维结构」根本画不出来——只靠合并符号字形是不够的
（合进去的 ∑ ∫ ∏ 只是单个字形，无法做上下限、无法伸缩）。

方案：复用 MarkdownToHandwrite（MIT）的 2D 公式排版内核（见 vendor/），
只保留「LaTeX 解析 → 盒式布局 → 笔画化渲染」，不启用它的 SDT 轨迹包、
也不走它的整页 PDF 排版。这样我们拿到的是一张透明 RGBA + 基线，
可以直接贴到 hwrender 的 4x 画布上，和正文共用同一套扰动管线。

对外接口（单位都是 hwrender 的内部 px，即 pt * SCALE）：
    measure(latex, size_px) -> (w, asc, desc)
    draw(img, latex, x, baseline, size_px) -> w
    has_formula(text) -> bool
"""
import os
import sys
import threading
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(HERE, "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

_lock = threading.Lock()
_engine = None
_renderer = None
_engine_key = None

# 上游把希腊字母映射成形近汉字（如 \alpha -> 伪），因为它的默认字体没有希腊字形。
# 我们的 *-merged.ttf 已经并入了真希腊字母，必须改回真字符，否则公式里会出现汉字。
_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "omicron": "ο", "pi": "π", "varpi": "π", "rho": "ρ", "varrho": "ρ",
    "sigma": "σ", "varsigma": "ς", "tau": "τ", "upsilon": "υ", "phi": "φ",
    "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ",
    "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}

# 上游若用形近汉字表示的数学符号，这里一并改回真字符
_SYMBOLS = {
    "sum": "∑", "int": "∫", "prod": "∏", "iint": "∬", "iiint": "∭",
    "partial": "∂", "nabla": "∇", "in": "∈", "notin": "∉", "subset": "⊂",
    "subseteq": "⊆", "cup": "∪", "cap": "∩", "forall": "∀", "exists": "∃",
    "to": "→", "rightarrow": "→", "leftarrow": "←", "Rightarrow": "⇒",
    "Leftarrow": "⇐", "Leftrightarrow": "⇔", "perp": "⊥", "parallel": "∥",
    "langle": "⟨", "rangle": "⟩", "lceil": "⌈", "rceil": "⌉",
    "lfloor": "⌊", "rfloor": "⌋", "cdots": "⋯", "vdots": "⋮",
    "infty": "∞", "angle": "∠", "degree": "°", "pm": "±", "mp": "∓",
    "times": "×", "div": "÷", "cdot": "·", "approx": "≈", "le": "≤",
    "leq": "≤", "ge": "≥", "geq": "≥", "ne": "≠", "neq": "≠",
    "sqrt": "√", "infty ": "∞",
}


def _ensure_engine(font_path, size_px, ink):
    """按 (字体, 字号, 墨色) 缓存引擎——它内部要做字形笔画提取，比较贵。"""
    global _engine, _renderer, _engine_key
    key = (font_path, size_px, ink)
    if _engine_key == key and _renderer is not None:
        return _renderer
    with _lock:
        if _engine_key == key and _renderer is not None:
            return _renderer
        from mthw import config as mconfig
        from mthw import math_renderer as mr
        from mthw.handwriting import HandwritingEngine

        # 覆盖符号表：用真字符，不用形近汉字
        mr.COMMAND_SYMBOLS.update(_GREEK)
        mr.COMMAND_SYMBOLS.update(_SYMBOLS)

        cfg = mconfig.HandwritingConfig(
            font_path=font_path,
            math_font_path=font_path,
            fallback_font_path=font_path,
            ink_color="#%02x%02x%02x" % tuple(ink),
            body_font_pt=size_px / 4.0,
            math_font_pt=size_px / 4.0,
            sdt_trajectory_enabled=False,     # 不依赖 SDT 轨迹包
            prefer_handright=False,           # 不依赖 handright
        )
        _engine = HandwritingEngine(cfg, reference_size_px=int(size_px),
                                    math_reference_size_px=int(size_px))
        _renderer = mr.FormulaRenderer(_engine, seed="pdf-layer")
        _engine_key = key
        return _renderer


def _box(latex, size_px, font_path, ink):
    r = _ensure_engine(font_path, size_px, ink)
    # max_width 给足：公式自身的换行交给 hwrender 的 wrap 处理
    return r.render_inline(latex, int(round(size_px)), 100000)


@lru_cache(maxsize=512)
def _cached_box(latex, size_px, font_path, ink):
    """公式渲染不便宜（要提取字形笔画），而 measure 与 draw 会各调一次，必须缓存。

    返回的 MathBox.image 只被 alpha_composite 读取，不会被修改，可安全复用。
    """
    return _box(latex, size_px, font_path, ink)


def measure(latex, size_px, font_path, ink=(30, 44, 70)):
    """返回 (宽, 基线以上高度, 基线以下高度)，单位 px。"""
    b = _cached_box(latex, size_px, font_path, tuple(ink))
    return b.width, b.baseline, b.height - b.baseline


def draw(img, latex, x, baseline, size_px, font_path, ink=(30, 44, 70)):
    """把公式画到 img 上，使公式基线与 baseline 对齐；返回宽度（px）。"""
    b = _cached_box(latex, size_px, font_path, tuple(ink))
    img.alpha_composite(b.image, (int(round(x)), int(round(baseline - b.baseline))))
    return b.width


def has_formula(text):
    return "$" in text
