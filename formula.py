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

# 公式字号补偿系数：内核按 size_px 排出的主字符只有汉字的约 62% 高，
# 乘 1.65 后与正文汉字齐平（实测 45px vs 45px @ 54px 字号）。
MATH_SIZE_COMP = 1.65

# 是否把公式符号用 handstroke 二次笔迹化。
# 符号来自手写字库时应为 False（保留原笔迹）；
# 只有符号仍是印刷体字形时才需要 True。
USE_STROKE_PATCH = False

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


def _ensure_engine(font_path, size_px, ink, math_font_path=None):
    """按 (字体, 字号, 墨色) 缓存引擎——它内部要做字形笔画提取，比较贵。"""
    global _engine, _renderer, _engine_key
    math_font_path = math_font_path or font_path
    key = (font_path, math_font_path, size_px, ink)
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
            math_font_path=math_font_path,
            fallback_font_path=font_path,
            ink_color="#%02x%02x%02x" % tuple(ink),
            body_font_pt=size_px / 4.0,
            math_font_pt=size_px / 4.0,
            sdt_trajectory_enabled=False,     # 不依赖 SDT 轨迹包
            prefer_handright=False,           # 不依赖 handright
            # 字号补偿后公式字形变大，字重倍率回调到 1.15：
            # 再低（0.95）虽然笔画更细，但边缘全靠抗锯齿，反而显得发虚。
            # 本地改动：直接用字体原字形，不做任何二次加工。
            # 手写感已经由字库本身提供（ZTi-Sym 是真人笔迹），再叠加
            # 形变/字重/墨色后处理只会把字形改糊——实测公式锐度只有汉字的 58%、
            # 笔画被插值撑粗 1.5 倍。
            second_layer_enabled=False,       # 关掉形变/字重/墨色后处理
            stroke_weight_base_scale=1.0,     # 不再额外加粗
            ink_density_jitter=0.0,           # 不做墨色抖动
            dry_brush_probability=0.0,        # 不要飞白
            elastic_strength_ratio=0.0,       # 不做弹性形变
            baseline_wave_amplitude_ratio=0.0,# 不做基线起伏
            stroke_weight_variation_sigma_ratio=0.0,
        )
        _engine = HandwritingEngine(cfg, reference_size_px=int(size_px),
                                    math_reference_size_px=int(size_px))
        # 公式里的 Ω φ π ∑ √ 等符号：早先来自印刷体字形，所以用 handstroke
        # 把轮廓骨架化成「笔道」。现在符号字库（ZTi-Sym）本身就是真人手写，
        # 再骨架化会把原笔迹丢掉、换成程序描的粗糊线条——用户反馈
        # 「∑ 用到的字体不对」就是这个原因。
        # 所以默认不再二次笔迹化；需要旧行为时设 USE_STROKE_PATCH=True。
        if USE_STROKE_PATCH:
            import handstroke
            handstroke.inline_patch(_engine)
        _renderer = mr.FormulaRenderer(_engine, seed="pdf-layer")
        _engine_key = key
        return _renderer



def _box(latex, size_px, font_path, ink, math_font_path=None, display=False):
    """渲染公式：统一固定字号，不做逐公式的高度闭环。

    为什么不做闭环：闭环按「墨迹高 vs 目标高」迭代求缩放，但结果依赖
    缓存状态（lru_cache 命中时直接返回旧盒、未命中时重算），
    同一个公式因此会在两个尺寸之间跳变（实测 ∑ 在 76px / 42px 间跳）。
    公式字号统一，才是可预期的行为。
    """
    r = _ensure_engine(font_path, size_px, ink, math_font_path)
    box = _render_at(r, latex, size_px, MATH_SIZE_COMP, display)
    return box


def _render_at(r, latex, size_px, k, display):
    px = int(round(size_px * k))
    if display:
        # 大型运算式：走 display 布局，∑ ∫ 的上下限才会「上下堆叠」而不是侧挂，
        # 分式/根号也会按 display 尺寸放大。
        box = r._layout_with_weight_group(
            _parse_display(latex), px, "display", display_style=True)
    else:
        box = r.render_inline(latex, px, 100000)
    return box


def _parse_display(latex):
    """把 LaTeX 源码解析成节点树（display 模式用）。"""
    from mthw.math_renderer import LatexMathParser
    return LatexMathParser(latex).parse()


@lru_cache(maxsize=512)
def _cached_box(latex, size_px, font_path, ink, math_font_path, display=False):
    """公式渲染不便宜（要提取字形笔画），而 measure 与 draw 会各调一次，必须缓存。

    返回的 MathBox.image 只被 alpha_composite 读取，不会被修改，可安全复用。
    """
    return _box(latex, size_px, font_path, ink, math_font_path, display)


def measure(latex, size_px, font_path, ink=(16, 16, 18), math_font_path=None,
            display=False):
    """返回 (宽, 基线以上高度, 基线以下高度)，单位 px。"""
    b = _cached_box(latex, size_px, font_path, tuple(ink), math_font_path, display)
    return b.width, b.baseline, b.height - b.baseline


def draw(img, latex, x, baseline, size_px, font_path, ink=(16, 16, 18),
         math_font_path=None, display=False):
    """把公式画到 img 上，使公式基线与 baseline 对齐；返回宽度（px）。"""
    b = _cached_box(latex, size_px, font_path, tuple(ink), math_font_path, display)
    img.alpha_composite(b.image, (int(round(x)), int(round(baseline - b.baseline))))
    return b.width


def has_formula(text):
    return "$" in text
