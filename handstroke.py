# -*- coding: utf-8 -*-
"""handstroke.py — 把印刷体数学符号「笔迹化」。

问题：`*-merged.ttf` 里的 Ω √ ≈ × ° ∑ π φ 等字形是从 Segoe UI Symbol 搬来的**印刷体轮廓**，
和周围的中文手写字迹放在一起一眼就能看出不是同一支笔写的。

做法：把字形栅格化 → Zhang-Suen 细化取中心线（骨架）→ 用变宽笔画重绘。
得到的是一条条「笔道」而不是轮廓填充，天然带起收笔渐细、笔压抖动、轻微旋转，
观感与手写接近；形状仍是正确的数学符号。

注意：PIL 的 `font.getbbox()` 返回的是**布局框**（接近整个 em 框），不是墨迹框。
用它去缩放骨架会把上标 ² ³ 这类小字撑成正常大小，所以这里一律用栅格化后的
alpha bbox 作为真实墨迹框。
"""
import os
import sys
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
_VENDOR = os.path.join(HERE, "vendor")
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)

from mthw.sdt_renderer import font_glyph_strokes, render_strokes, TrajectoryStyle  # noqa: E402

# 需要笔迹化的字符：合并字体里来自 Segoe UI Symbol 的印刷体符号
STROKE_SYMBOLS = set(
    "Ωφω√≈∠²³·×−°θΔΣπ±≤≥≠∞→∘⌀"
    "∑∫∏∂∇∈⊂∪∩∀∃←⇒⇔⊥∥⟨⟩⌈⌉⌊⌋⋯⋮⁄"
    # 完整希腊字母：字体里是 Segoe 印刷字形，必须笔迹化，否则会像印刷体
    "αβγδεζηικλμνξοπρστυχψ"
    "ΓΔΘΛΞΠΥΦΨ"
)

# 笔迹风格：轻微旋转 + 低频坐标扰动 + 笔压抖动
# taper（起收笔渐细）和 width_jitter（笔压变化）必须很小：
# 大了会让 Ω ∑ 这类符号看起来像毛笔/马克笔写的「硬刷体」，正常人用中性笔写符号
# 是一根粗细基本均匀的线。
STYLE = TrajectoryStyle(
    rotation_sigma_deg=0.6,
    coordinate_jitter=0.35,
    jitter_correlation=9.0,
    width_jitter=0.04,
    taper=0.05,
    supersample=3,
)

# 笔宽相对字号的比值。手写笔道比印刷笔画粗，但太粗会和正文毛笔体脱节：
# 实测 0.068 时符号笔画中位游程 5–7px，而汉字只有 3–5px，符号看起来更「重」。
# 0.052 落到 4–5px，与正文一致。
_WIDTH_RATIO = 0.052

# 希腊字母用更细的笔道：它们在公式里字号被放大 1.65 倍，
# 按同一比例算出来的笔宽会粗得像马克笔（实测视觉上比汉字粗一倍）。
_GREEK = set("αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΥΦΨΩ")
_GREEK_WIDTH_RATIO = 0.032

# 大运算符（∑ ∫ ∏）在公式里会被额外放大，笔道再细一点才不会像刷子
_BIGOP = set("∑∫∏∬∭")
_BIGOP_WIDTH_RATIO = 0.040

# 公式里这些字符要额外缩小：公式整体字号有 1.65 倍补偿，拉丁字母大小刚好
# （实测 a/b/g/y 都是汉字的 1.0–1.1 倍），但希腊字母被放大到 1.3–1.8 倍。
#
# 不用一刀切：各字母的墨迹高差很多（β 同时有上伸和下伸，比 α 高 70%），
# 按实测比例逐个校准，让每个字母都落到汉字的 1.0 倍附近。
_LOWER_GREEK = set("αβγδεζηθικλμνξοπρστυφχψω")
_UPPER_GREEK = set("ΓΔΘΛΞΠΣΥΦΨΩ")

# 实测（公式渲染，汉字=1.00）：α1.02 β1.76 γ1.36 θ1.40 λ1.36 μ1.33
#   π1.00 ρ1.31 σ0.93 τ0.95 φ1.33 ω0.98 Ω1.19 Δ1.17 Σ0.93 ∑1.14
# 基准 shrink 取 0.82（已生效），这里再按各字母实际高度二次校准。
_GREEK_TUNE = {
    "β": 0.58, "γ": 0.76, "θ": 0.75, "λ": 0.76, "μ": 0.77, "ρ": 0.78,
    "φ": 0.77, "ψ": 0.78, "ξ": 0.80, "ζ": 0.85, "η": 0.82, "κ": 0.90,
    "ν": 0.90, "υ": 0.92, "δ": 0.74, "ε": 0.95, "ι": 0.95, "ο": 0.95,
    "ω": 1.00, "α": 1.00, "π": 1.00, "σ": 1.05, "τ": 1.05,
    "Ω": 0.80, "Δ": 0.80, "Θ": 0.86, "Φ": 0.80, "Ψ": 0.86, "Λ": 0.86,
    "Ξ": 0.95, "Π": 0.95, "Γ": 0.95, "Υ": 0.88, "Σ": 1.05,
}
_FORMULA_SHRINK_BIGOP = 0.70      # ∑ ∫ ∏
_FORMULA_SHRINK_GREEK = 0.82      # 希腊字母基准


def _formula_shrink(ch):
    """公式路径下该字符的缩放系数（正文路径不用）。"""
    if ch in _BIGOP:
        return _FORMULA_SHRINK_BIGOP
    if ch in _LOWER_GREEK or ch in _UPPER_GREEK:
        return _FORMULA_SHRINK_GREEK * _GREEK_TUNE.get(ch, 1.0)
    return 1.0

# render_strokes 内部会留 4.5% 的内边距，这里反向补偿，保证符号大小与字面一致
_FIT = 1.0 / (1.0 - 2 * 0.045)
_TILE_PAD = 6

# 墨色浓度补偿（alpha 伽马，越小越实）：把符号/公式的墨色补到与正文汉字一致
_SYMBOL_GAMMA = 0.68
FORMULA_GAMMA = 0.50


@lru_cache(maxsize=256)
def _strokes(font_path, ch):
    """提取字形骨架（昂贵，必须缓存）。"""
    try:
        return font_glyph_strokes(font_path, ch)
    except Exception:
        return ()


@lru_cache(maxsize=512)
def _ink_box(font_path, ch, size_px):
    """返回 (墨迹框宽, 墨迹框高, 墨迹相对笔位的偏移 x, 偏移 y, 布局框 l, 布局框 t)。

    偏移量是「墨迹左上角相对笔位（基线起点）」的距离，y 向上为负。
    """
    font = ImageFont.truetype(font_path, size_px)
    l, t, r, b = font.getbbox(ch)
    probe = 4
    canvas = Image.new("L", (max(1, r - l) + 2 * probe, max(1, b - t) + 2 * probe), 0)
    ImageDraw.Draw(canvas).text((probe - l, probe - t), ch, fill=255, font=font)
    bb = canvas.getbbox()
    if bb is None:
        return None
    # canvas 内笔位在 (probe - l, probe - t)；换算成相对笔位的墨迹偏移
    x0 = bb[0] - (probe - l)
    y0 = bb[1] - (probe - t)
    w = max(1, bb[2] - bb[0])
    h = max(1, bb[3] - bb[1])
    return w, h, x0, y0, l, t


def available(ch):
    return ch in STROKE_SYMBOLS


# 骨架提取对「细长笔画 + 紧邻平行线」的符号会破坏拓扑（≤ ≥ 糊成一块、∠ 退化成横线、
# 小圆圈变方块），这几个改用人工描的笔道。坐标是 0..1 归一化，由 render_strokes 缩放。
def _circle(cx=0.5, cy=0.5, r=0.46, n=36):
    t = np.linspace(0.0, 2.0 * np.pi, n)
    return np.stack([cx + r * np.cos(t), cy + r * np.sin(t)], axis=1).astype(np.float32)


def _poly(*pts):
    return np.array(pts, dtype=np.float32)


_CUSTOM = {
    # 度符号：小圆圈
    "°": lambda: (_circle(0.5, 0.5, 0.45),),
    # 复合函数圈：略大一点的圆
    "∘": lambda: (_circle(0.5, 0.5, 0.44),),
    # 求和号 ∑ / 大写 Σ：上横、斜线、下横 三笔。
    # 骨架提取对这个字形完全失败（实测被切成 160+ 段碎片，还会凭空长出竖线），
    # 所以必须人工描。方向要点（照印刷字形简化，去掉上下横右端的竖钩）：
    #   · 上横：从左端拉到右上角
    #   · 斜线：从**左上**附近起，向右下走到中点，再折向左下到底边（Σ 的中间折）
    #   · 下横：从左端拉到右端
    # 早先两版分别画成「<」形和「Z」形，都不对。
    "∑": lambda: (_poly((0.06, 0.05), (0.94, 0.05)),
                  _poly((0.16, 0.09), (0.86, 0.50), (0.16, 0.91)),
                  _poly((0.06, 0.95), (0.94, 0.95))),
    "Σ": lambda: (_poly((0.06, 0.05), (0.94, 0.05)),
                  _poly((0.16, 0.09), (0.86, 0.50), (0.16, 0.91)),
                  _poly((0.06, 0.95), (0.94, 0.95))),
    # 角：顶点在左下，一条水平射线 + 一条斜射线
    "∠": lambda: (_poly((0.10, 0.86), (0.90, 0.86)),
                  _poly((0.10, 0.86), (0.90, 0.14))),
    # 小于等于：< 的两笔 + 下横线
    "≤": lambda: (_poly((0.88, 0.08), (0.14, 0.36)),
                  _poly((0.14, 0.36), (0.88, 0.64)),
                  _poly((0.14, 0.92), (0.88, 0.92))),
    # 大于等于：镜像
    "≥": lambda: (_poly((0.12, 0.08), (0.86, 0.36)),
                  _poly((0.86, 0.36), (0.12, 0.64)),
                  _poly((0.12, 0.92), (0.86, 0.92))),
}


def _custom_strokes(ch):
    maker = _CUSTOM.get(ch)
    return maker() if maker else None


def tile(ch, size_px, ink, font_path, seed=0, shrink=1.0):
    """返回 (RGBA 贴图, ox, oy)，与 hwrender._char_tile 的契约完全一致。

    贴图内：笔位在 (pad - l, pad - t)，墨迹按字体真实位置摆放，
    因此基线与字宽和普通文字对齐，不会因笔迹化而漂移。
    """
    box = _ink_box(font_path, ch, size_px)
    if box is None:
        return None, 0, 0
    ink_w, ink_h, x0, y0, l, t = box

    strokes = _custom_strokes(ch) or _strokes(font_path, ch)
    if not strokes:
        return None, 0, 0

    # shrink < 1 时把墨迹整体缩小，但仍居中在原墨迹框内——
    # 这样基线不变，只是符号本身变小（用于公式里被放大 1.65 倍的 ∑/Ω 等）。
    draw_w = max(1, int(round(ink_w * shrink)))
    draw_h = max(1, int(round(ink_h * shrink)))
    inset_x = (ink_w - draw_w) / 2.0
    inset_y = (ink_h - draw_h) / 2.0

    # 骨架按真实墨迹框缩放；_FIT 抵消 render_strokes 的内部留白
    box_w = max(2, int(round(draw_w * _FIT)))
    box_h = max(2, int(round(draw_h * _FIT)))
    rng = np.random.default_rng(abs(hash((ch, size_px, seed))) % (2 ** 63))
    if ch in _GREEK:
        ratio = _GREEK_WIDTH_RATIO
    elif ch in _BIGOP:
        ratio = _BIGOP_WIDTH_RATIO
    else:
        ratio = _WIDTH_RATIO
    # 缩小后笔画也应同比例变细，否则小字形配粗笔道会糊成一团
    stroke_w = max(1.2, size_px * ratio * shrink)
    glyph = render_strokes(strokes, box_w, box_h, stroke_w, ink, rng, STYLE,
                           em_size=size_px)
    # 墨色补到正文水平（符号原本平均 alpha≈139，正文汉字≈170）
    glyph = _alpha_boost(glyph, _SYMBOL_GAMMA)

    layout_w = max(1, int(round(font_layout_width(font_path, ch, size_px))))
    canvas_w = max(layout_w, ink_w + 2 * _TILE_PAD)
    canvas_h = max(ink_h + 2 * _TILE_PAD, box_h)
    canvas = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    # 墨迹左上角在贴图内的位置：笔位(pad - l, pad - t) + 相对偏移(x0, y0) + 缩小留白
    px0 = _TILE_PAD - l + x0 + inset_x
    py0 = _TILE_PAD - t + y0 + inset_y
    # 以墨迹框中心对齐（box 比墨迹大一点，需要回退半个差值）
    dx = px0 - (box_w - draw_w) / 2.0
    dy = py0 - (box_h - draw_h) / 2.0
    canvas.alpha_composite(glyph, (int(round(dx)), int(round(dy))))
    return canvas, _TILE_PAD - l, _TILE_PAD - t


@lru_cache(maxsize=512)
def font_layout_width(font_path, ch, size_px):
    font = ImageFont.truetype(font_path, size_px)
    return font.getlength(ch)


def _alpha_boost(img, gamma):
    """保形提亮 alpha：gamma < 1 时中间调变实，形状/边缘抗锯齿不变。

    为什么需要：正文汉字来自马善政毛笔体，笔画本身浓（平均 alpha≈170）；
    符号是「骨架 + 变宽笔道」重绘、公式走另一套引擎的 alpha 调制，二者墨色都偏淡
    （实测符号≈139、公式≈110），放在一起就是「粗细不统一」。
    用 alpha 伽马把它们的浓度补到同一水平，比加粗笔画更自然（不会把字变胖）。
    """
    if abs(gamma - 1.0) < 1e-6:
        return img
    lut = [min(255, int(round(255.0 * (i / 255.0) ** gamma))) for i in range(256)]
    out = img.copy()
    out.putalpha(img.getchannel("A").point(lut))
    return out


def _draw_text(glyph, x, y, char, font, ink):
    """画普通字形。

    试过用 stroke_width 给公式字体加粗以对齐正文毛笔体的笔画重量，
    但加粗后笔画变成等宽、反而像无衬线印刷体，丢掉手写感，故不加粗。
    """
    ImageDraw.Draw(glyph).text((x, y), char, fill=ink, font=font)


def inline_patch(engine):
    """让公式内核在画普通字符时，也把符号换成笔迹。

    上游 `_render_with_pillow` 是逐字 `draw.text`；这里替换成「符号走笔迹、
    其余照旧」的版本。函数体与上游保持一致，只改了符号分支。
    """
    import random as _random
    import types

    from mthw.handwriting import _crop_alpha, _load_font

    def _render_with_pillow(self, text, size_px, max_width, seed, math=False):
        rand = _random.Random(seed)
        x_sigma = self._glyph_position_sigma(size_px, self.config.perturb_x_sigma_px)
        y_sigma = self._vertical_position_sigma(size_px, math_mode=math)
        pad = max(round(size_px * 0.4), self._position_jitter_padding(x_sigma, y_sigma))
        runs = self._font_runs(text, size_px, math=math)
        reference_ascent = self._reference_ascent(runs, size_px)
        descent = max(_load_font(p, size_px).getmetrics()[1] for p, _ in runs)
        max_adjustment = max(self._glyph_vertical_adjustment(c, size_px) for c in text)
        line_height = max(round(size_px * self.config.line_spacing),
                          reference_ascent + descent + 2 * pad + max_adjustment)
        width = self._line_canvas_width(text, size_px, pad, math_mode=math)
        image = Image.new("RGBA", (width, line_height), (0, 0, 0, 0))
        x = pad
        baseline = pad + reference_ascent
        for char in text:
            font_path = self._font_path_for_char(char, size_px, math=math)
            font = _load_font(font_path, size_px)
            if char.isspace():
                x += self.character_advance(char, size_px, math=math)
                continue
            bbox = font.getbbox(char)
            if bbox is None:
                x += self._sample_character_advance(char, size_px, rand, math=math)
                continue
            char_w = max(1, bbox[2] - bbox[0])
            glyph = Image.new("RGBA", (char_w + 2 * pad, line_height), (0, 0, 0, 0))
            glyph_y = baseline - font.getmetrics()[0] + self._glyph_vertical_adjustment(char, size_px)
            if available(char):
                t, ox, oy = tile(char, size_px, self.ink, font_path,
                                 seed=rand.randrange(1 << 30),
                                 shrink=_formula_shrink(char))
                if t is not None:
                    # 笔位对齐：贴图内笔位在 (pad - l, pad - t)，这里把笔位放到 (pad, glyph_y)
                    glyph.alpha_composite(
                        t, (int(round(pad - ox)), int(round(glyph_y - oy))))
                else:
                    _draw_text(glyph, pad, glyph_y, char, font, self.ink)
            else:
                _draw_text(glyph, pad, glyph_y, char, font, self.ink)
            angle = rand.gauss(0, self._glyph_rotation_sigma_degrees())
            if angle:
                glyph = glyph.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False)
            image.alpha_composite(
                glyph,
                (round(x - pad + self._sample_position_offset(rand, x_sigma)),
                 round(self._sample_position_offset(rand, y_sigma))),
            )
            x += self._sample_character_advance(char, size_px, rand, math=math)
        return _crop_alpha(image, pad=4)

    engine._render_with_pillow = types.MethodType(_render_with_pillow, engine)
    return engine
