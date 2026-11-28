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

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(HERE, "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

_lock = threading.Lock()
_engine = None
_renderer = None
_engine_key = None

# 公式字号补偿：内核按「声明字号」画出的字形与 PIL 是 1:1 的，所以直接
# 乘 fontcal 给这套符号字库算出的 size_scale 即可（统一基础参数）。
# 以前这里写死 1.65（按已移出的马善政体标定），而且 4 套符号轮换共用同一个
# 倍率——同一段里数字会随轮换在 45~52px 之间跳，比汉字大一截。
# MATH_SIZE_COMP 只作为 fontcal 不可用时的兜底。
MATH_SIZE_COMP = 1.65


def size_comp(math_font_path):
    """这套公式字体该用的字号倍率（= fontcal 的 size_scale）。"""
    try:
        import fontcal
        s = float(fontcal.params_for(math_font_path).get("size_scale", 1.0) or 1.0)
        if s > 0:
            return s
    except Exception:
        pass
    return MATH_SIZE_COMP


def _same_pen_hook(img, size_px, renderer):
    """公式里缩小渲染的文字（分式上下、上下标）把笔宽/墨色补回正文。

    字号一小，字库的笔宽按比例变细：正文 4px，0.58 倍的角标只剩 2.3px。
    同一句里两种粗细，看着就不是一支笔（用户反馈）。
    """
    try:
        import fontcal
        ref = float(getattr(renderer, "ref_size_px", size_px) or size_px)
        if size_px >= ref - 0.5:
            return img
        ratio = getattr(renderer, "pen_em_ratio", None) or 0.056
        shrink = min(1.0, max(0.0, 1.0 - size_px / ref))
        # 0.9：补到正文笔宽的九成（补满会把小字母的字孔糊住，见 hwrender）
        return fontcal.thicken_alpha(img, 0.9 * ratio * (ref - size_px) / 2.0,
                                     gain=1.0 + min(0.08, 0.12 * shrink))
    except Exception:
        return img

# 公式层字形调细：数字/拉丁/希腊与分式横线都走公式层，ZTi-Sym 的笔画比汉字略粗，
# 整段里数字显得发黑。这里把位图 alpha 的低值端压掉（柔和收边，不产生锯齿），
# 让数字/字母笔画与汉字接近。数值是 alpha 阈值：0 = 关闭，140 = 默认（比原件细一点）。
# 可用环境变量 FORMULA_THIN_LEVEL 覆盖。
#
# 2026-11-26 起默认关闭（用户反馈「公式里的除号、根号太细太淡」）：
# 当年是因为 ZTi-Sym 笔画比汉字粗才加的这一层；现在字库全部换成同一支笔写的，
# 实测三套字库的笔宽都是 4px、墨色都在 220 上下，再压一遍反而让公式比正文淡一档。
# 真要细一点可用 FORMULA_THIN_LEVEL=60 之类开回去。
try:
    FORMULA_THIN_LEVEL = int(float(os.environ.get("FORMULA_THIN_LEVEL", "0")))
except Exception:
    FORMULA_THIN_LEVEL = 0

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

        import fontcal
        cfg = mconfig.HandwritingConfig(
            font_path=font_path,
            math_font_path=math_font_path,
            fallback_font_path=font_path,
            ink_color="#%02x%02x%02x" % tuple(ink),
            body_font_pt=size_px / float(fontcal.SCALE),
            math_font_pt=size_px / float(fontcal.SCALE),
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
        # 规则线（分数线/根号横）用这套字库自己的笔宽，而不是内核写死的
        # 0.024 em。分数线和根号是「程序画的线」，不跟着字库走就会明显比
        # 正文细一档——用户反馈的「除号、根号太细太淡」就是它。
        try:
            px_render = size_px * size_comp(math_font_path)
            _renderer.pen_em_ratio = fontcal.pen_em_ratio(math_font_path,
                                                          px_render)
            _renderer.ref_size_px = px_render
        except Exception:
            _renderer.pen_em_ratio = None
            _renderer.ref_size_px = size_px
        # 同一支笔：公式文字缩小渲染后补回笔宽（见 _same_pen_hook）
        mr.TEXT_TILE_HOOK = _same_pen_hook
        _engine_key = key
        return _renderer



def _thin_formula_image(img, level):
    """按「笔画粗细自适应」调细公式位图。

    用户反馈：数字整体偏粗，但简单地把 alpha 整体压低后，分式的分子/分母
    和上下角标（字号更小、笔画本来就细）会变得特别淡。
    所以这里只对**粗笔画**（局部笔画半径 ≥ 阈值）的外沿收一点边：
      · 基准字号的数字/字母（粗）→ 视觉上细一档；
      · 分式上下、上下角标（细）→ 完全不碰，保持原墨色。
    level: 0 = 关闭；140 = 默认（≈2 档）；越大收得越多。
    """
    if img is None or img.mode != "RGBA" or level <= 0:
        return img
    try:
        import inkproc
        return inkproc.thin_rgba(img, max(0.5, float(level) / 70.0))
    except Exception:
        return img


def _box(latex, size_px, font_path, ink, math_font_path=None, display=False):
    """渲染公式：统一固定字号，不做逐公式的高度闭环。

    为什么不做闭环：闭环按「墨迹高 vs 目标高」迭代求缩放，但结果依赖
    缓存状态（lru_cache 命中时直接返回旧盒、未命中时重算），
    同一个公式因此会在两个尺寸之间跳变（实测 ∑ 在 76px / 42px 间跳）。
    公式字号统一，才是可预期的行为。倍率来自 fontcal（每套字库一个值），
    4 套符号轮换出来的字高因此一致。
    """
    math_font_path = math_font_path or font_path
    r = _ensure_engine(font_path, size_px, ink, math_font_path)
    box = _render_at(r, latex, size_px, size_comp(math_font_path), display)
    if FORMULA_THIN_LEVEL > 0 and int(size_px) >= 44:
        try:
            box.image = _thin_formula_image(box.image, FORMULA_THIN_LEVEL)
        except Exception:
            pass
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
