# -*- coding: utf-8 -*-
"""hwrender.py — 手写风格文本层渲染引擎（PDF-Layer 本地重建版）

设计目标：把一段文本渲染成「远看是手写」的透明 PNG，并可注回 PDF 原版面空白区。

相对交接稿 5 节的升级（对应 6.1 / 6.2）：
  6.1 同字异形：逐字独立采样 基线漂移 / 水平偏移 / 字号抖动 / 单字旋转 / 墨色深浅
  6.2 笔画层扰动：整行低频正弦弯曲 + 微剪切 + 行首宽行尾紧的字距渐变
  6.5 字形合并：使用 *-merged.ttf（已把 Ω φ √ ≈ ∠ ² ³ 等符号并入中文手写字体），
      不再需要逐字符回退 DejaVuSans，符号也是手写字形。

单位约定：外部坐标一律 pt；内部渲染 SCALE 倍超采样。
"""
import math
import os
import random

from PIL import Image, ImageDraw, ImageFont

import fontcal
import formula
import handstroke

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

SCALE = 4                      # 1pt = 4px
# 手写字要比印刷正文大一号：真人很难把字写得跟印刷体一样小、一样匀。
# 13.5pt 偏大（整段占页太多、符号也跟着变大），12.4pt 是「明显大于印刷体」又不夸张的值。
DEFAULT_SIZE_PT = 12.4

# 数字/拉丁/希腊字形（来自 ZTi-Sym 手写符号库）比汉字笔画略粗：
# 实测同字号下笔宽 4.5px vs 汉字 4.0px（4x 画布），整段看起来「数字发黑」。
# 渲染时对这类字形把 alpha 收边 SYMBOL_THIN px（4x 像素；0 = 关闭）。
# 只影响非汉字（数字/拉丁/希腊），汉字、标点、公式层不动。
SYMBOL_THIN = 1.0
DEFAULT_LINE_PT = 23.0         # 行距保持约 1.85 倍字号
INK = (16, 16, 18)             # 墨色 RGB：黑笔（alpha 逐字抖动）
PAGE_LEFT_PT = 64.0
PAGE_RIGHT_PT = 468.0
BLOCK_GAP_PT = 15.0
PAGE_BOTTOM_PT = 676.0         # 触发降字号重排的底限（页码在 ~690pt）

NO_LINE_START = "，。；、：？！）》」』”’%,.;:?!)]}"
NO_LINE_END = "（《「『“‘([{"
_TILE_PAD = 6                   # 单字贴图四周的透明内边距（px），排版时不能算进字距
_JITTER_INFLATE = 0.02          # 逐字扰动（缩放/位移/防重叠右推）平均把行宽撑大的比例
_LINE_HEIGHT_DAMP = 0.80        # 超高行（分式/根号）额外高度的打折系数：
                                # 1.0=完全按内容给（行距忽大忽小），0.55=压太紧会与下一行相撞
_LINE_HEIGHT_RATIO = 1.85

# ---- 上下标 ----
_SUP_SCALE = 0.60          # 上下标字号 = 正文 × 0.6
_SUP_RISE = 0.42           # 上标基线上移量（em）
_SUB_DROP = 0.12           # 下标基线下移量（em）
# 字体里本来就按「角标」画的字符。scanfont 建库时把墨迹底边当基线，
# 所以 ² ³ ° ′ ″ 这些原本该浮在上面的字，全被压到基线上了——
# 实测 ° 的 bbox 是 81..114（基线 114），而数字是 57..114，
# 也就是 ° 只有半个字高且坐在基线上，看着像字母 o（30° 被读成 30o/300）。
# 这里统一把它们抬起 0.38em，恢复到角标位置。
_RAISE_CHARS = "⁰¹²³⁴⁵⁶⁷⁸⁹°′″ⁿ˚"
_RAISE_EM = 0.38

# 贴底标点：它们没有「字高」概念，只需坐在基线附近。
# 不能参与逐字高度闭环——闭环会把它们放大，底边跟着上移，
# 句号就会从底部浮到中间（用户反馈「句号没贴着底部」）。
# 全角中文标点（，。、；：？！…—～）也必须在这里：它们同样没有字高概念，
# 若走「按回退字体同字高度缩放」会被老 ZTi 的小标点带偏（实测全角冒号缩到 9px）。
_BOTTOM_MARKS = "。，、．,.;:…·；：？！—～－"

# 纯文本里哪些字符改用公式渲染（与 $...$ 完全同源，保证粗细墨色一致）。
# 汉字不走——公式引擎的汉字来自字体度量，和手写汉字库风格不搭。
_FORMULA_CHARS = set(
    "0123456789"
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "+-−×÷=≠≈≡<>≤≥±∓·/\\"
    "()[]{}|.,;:!?"
    "αβγδεζηθικλμνξοπρστυφχψωΓΔΘΛΞΠΣΦΨΩ"
    "√∞∂∇∑∏∫∬°℃%‰"
)
# 这些字符在 LaTeX 里要转义
_TEX_ESCAPE = {"%": r"\%", "#": r"\#", "&": r"\&", "_": r"\_",
               "{": r"\{", "}": r"\}", "$": r"\$"}


def _formula_ready(ch):
    """该字符是否适合交给公式渲染。"""
    return ch in _FORMULA_CHARS


def _tex_char(ch):
    """把单个字符转成可安全解析的 LaTeX 片段。"""
    return _TEX_ESCAPE.get(ch, ch)       # 行距 = 字号 × 该比例（字号调大时行距自动跟随）

# 常见固定说法：换行时尽量不把它们从中间劈开（真人手写宁可挤一点也不拆词）。
# 不追求完整词典，只收本科理科作业里高频的搭配，避免 DP 为填满行而硬拆。
_PHRASES = (
    "三相电路", "线电压", "相电压", "相电流", "线电流", "有功功率", "无功功率",
    "视在功率", "功率因数", "星形联结", "三角形联结", "并联接入", "三相电源",
    "中性线", "阻抗", "电阻", "电感", "电容", "频率", "相位", "有效值",
    "最大值", "平均值", "瞬时值", "参考方向", "关联参考方向", "基尔霍夫",
    "节点电压", "回路电流", "叠加定理", "戴维南", "诺顿", "最大功率",
    "串联", "并联", "混联", "负载", "电源", "电路", "如图", "所示", "已知",
    "试求", "求", "解", "因此", "所以", "因为", "由于", "可得", "代入",
    "整理得", "即", "故", "则", "当", "若", "设", "令",
)
_SYM_CP = set(ord(c) for c in "Ωφω√≈∠²³·×−°θΔΣπ±≤≥≠∞→∘⌀")

# ---- 汉字识别 / 基准字高 ----
# 手写汉字字库（ZTi-Han 等）只覆盖有限字表，缺字时按 han_fallback_fonts 回退；
# 回退字体与主字体的字高不同，需要一个「主字体汉字基准高」来对齐。
_CJK_REF_SAMPLE = "相电压功率流生电的"     # 366 字表内的常用字，用于量主字体汉字高度


def _is_plain_symbol(ch):
    """是否为「数字/拉丁/希腊」字形（这些来自 ZTi-Sym，笔画偏粗，需要收边）。"""
    return (ch.isascii() and ch.isalnum()) or ("\u0370" <= ch <= "\u03ff")


def _thin_alpha_tile(img, amount):
    """把 RGBA 贴图的 alpha 柔和收边（与公式层同一套口径）。

    amount 为 alpha 阈值（0=关闭，140=默认）：压掉 alpha 低值端再拉伸，
    笔画视觉上细一点且边缘平滑；颜色不动，只用于数字/拉丁/希腊字形。
    """
    if amount <= 0:
        return img
    import numpy as np
    a = np.asarray(img.getchannel("A")).astype(np.float32)
    t = float(min(240, max(1, int(140 * float(amount)))))
    a = np.clip((a - t) * 255.0 / (255.0 - t), 0, 255)
    out = img.copy()
    out.putalpha(Image.fromarray(a.astype(np.uint8), "L"))
    return out


def _is_cjk(ch):
    """是否表意汉字（只认基本区：回退链/校准都只针对这些字）。"""
    cp = ord(ch)
    return 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF


def _is_cjk_punct(ch):
    """全角中文标点/括号：本身占满字面或贴底，不能按「数字 0.86 倍」缩小。

    这些字符在换用回退字体量高度时若乘 0.86，括号会缩成 36px（正常 44px）。
    """
    cp = ord(ch)
    return (0x3000 <= cp <= 0x303F          # 、。〈〉《》「」【】〜
            or 0xFF01 <= cp <= 0xFF20       # ！＂＃…＠（全角半区）
            or 0xFF3B <= cp <= 0xFF40       # ［＼］＾＿｀
            or 0xFF5B <= cp <= 0xFF65       # ｛｜｝～…
            or 0x2018 <= cp <= 0x201F       # ‘’“”
            or cp in (0x2013, 0x2014, 0x2026, 0x00B7))



# ---------------------------------------------------------------- 字体管理
_font_cache = {}


def _pick_math_font(font_path):
    """给公式挑一款拉丁字形更清楚的手写字体。

    正文用的马善政楷书是连笔毛笔体，拉丁 `I` 会被读成 `9`；公式里 I（电流）极常见，
    所以默认换成同目录下的龙藏体（同样 OFL、同样是手写），字形清楚得多。
    找不到就退回正文字体。
    """
    d = os.path.dirname(os.path.abspath(font_path))
    for name in ("LongCang-Regular-merged.ttf", "LongCang-Regular.ttf"):
        p = os.path.join(d, name)
        if os.path.exists(p) and os.path.abspath(p) != os.path.abspath(font_path):
            return p
    return font_path


def _ttf(path, px):
    key = (path, px)
    f = _font_cache.get(key)
    if f is None:
        f = ImageFont.truetype(path, px)
        _font_cache[key] = f
    return f


class Hand:
    """一套手写字体 + 渲染参数。"""

    def __init__(self, font_path, size_pt=DEFAULT_SIZE_PT, ink=INK,
                 jitter=1.0, seed=20261118, math_font_path=None,
                 variant_fonts=None, symbol_font_path=None,
                 han_fallback_fonts=None, punct_ref_font=None,
                 symbol_thin=SYMBOL_THIN):
        self.font_path = font_path
        self.size_pt = size_pt
        self.ink = ink
        self.jitter = jitter          # 全局扰动强度倍率，0 = 印刷体
        self.rng = random.Random(seed)
        # 公式里的拉丁字母/数字换一款字形更清楚的同族手写字体（默认龙藏体）：
        # 马善政楷书的连笔 I 会被读成 9，公式里 I（电流）出现频率极高，必须可辨。
        self.math_font_path = math_font_path or _pick_math_font(font_path)
        # ---- 同字多形：额外几套「真人笔迹」字库（字嗨写 4 遍 / scanfont 扫 4 张）----
        # 主字体恒为第 0 套，并始终作为换行与字距的度量基准：多套字库的 advance
        # 可能略有差异，若逐字按各自字宽度量，行宽会漂移、禁则也会错位。
        self.variant_fonts = [font_path]
        for p in (variant_fonts or []):
            if os.path.abspath(p) != os.path.abspath(font_path):
                self.variant_fonts.append(p)
        self._char_seen = {}          # 字符 -> 已出现次数（决定下一次用哪套）
        self._cmap_cache = {}         # 字体路径 -> Unicode 覆盖集合
        self._cjk_ref_cache = {}      # 字号 -> 主字体汉字基准墨迹高（px）
        # 汉字缺字回退链：主字体（手写 366 字表）里没有的字，依次用这些字体补，
        # 都补不到才回退到主字体（会渲染成空白/豆腐块，正常字表内的字不会走到这里）。
        self.han_fallback_fonts = [p for p in (han_fallback_fonts or [])
                                   if p and os.path.exists(p)]
        # 中文标点/括号的大小参照字体（旧默认是马善政）。手写汉字库和符号库都
        # 不含「同字不同套」的标点大小基准，括号若按回退字体量高度会缩水 20%；
        # 传旧默认字体进来，标点大小就与接入前一致。
        self.punct_ref_font = (punct_ref_font
                               if punct_ref_font and os.path.exists(punct_ref_font)
                               else None)
        self.symbol_font_path = symbol_font_path or self._pick_symbol_font(font_path)
        # 数字/拉丁/希腊收边量（4x 像素）；0 = 原始粗细
        self.symbol_thin = float(symbol_thin or 0.0)
        self._variants_active = True  # 风格一致性护栏，见 _check_style_consistency

    def _with_fallback(self, ch, path):
        """主字体缺字时换全字表字体（自写字库如 ZTi 只覆盖部分汉字）。

        自写字库通常只覆盖正文用到的几十上百个字；拿它当主字体时，
        未覆盖的字若直接渲染会得到 .notdef 豆腐块。这里统一回退到
        symbol_font_path（全字表 *-merged 字体）。variants 激活时
        _variant_for 已有同类回退，这里补上单套模式与度量路径的缺口。
        """
        c0 = self._cmap(path)
        if c0 is not None and ord(ch) not in c0:
            fb = self.symbol_font_path
            fc = self._cmap(fb)
            if fc is None or ord(ch) in fc:
                return fb
        return path

    def _pick_symbol_font(self, font_path):
        """挑一份「肯定含数学符号」的字体，供缺字时兜底。

        用户手写/扫描的字库通常只覆盖正文那几十个字，不会去描 Ω √ ∑ 这类符号；
        若主字体和变体字体都缺符号，笔迹化拿不到轮廓，会渲染出豆腐块。
        主字体若已是 *-merged.ttf（注入过符号）就直接用它，保持旧行为不变。
        """
        c = self._cmap(font_path)
        if c is not None and all(ord(s) in c for s in "Ω√≈"):
            return font_path
        for name in ("MaShanZheng-Regular-merged.ttf", "LongCang-Regular-merged.ttf",
                     "ZhiMangXing-Regular-merged.ttf", "LiuJianMaoCao-Regular-merged.ttf"):
            p = os.path.join(FONT_DIR, name)
            if os.path.exists(p):
                return p
        return font_path

    def _cmap(self, path):
        """字库的 Unicode 覆盖集合，用于缺字回退；读不到则返回 None（不回退）。"""
        key = os.path.abspath(path)
        if key not in self._cmap_cache:
            try:
                from fontTools.ttLib import TTFont
                self._cmap_cache[key] = set(TTFont(path).getBestCmap() or {})
            except Exception:
                self._cmap_cache[key] = None
        return self._cmap_cache[key]

    def _variant_for(self, ch):
        """按「该字第几次出现」循环挑一套字库；自动跳过缺字的套。

        返回 None 表示沿用引擎默认的 _font_for(ch)（不启用同字多形）。
        拉丁字母/数字默认不参与循环：引擎专门让它们走 math_font_path 的硬笔
        写法以避免毛笔连笔刷体，这里不能把那条调参覆盖掉。

        注意：字库覆盖率不足时会被 _check_style_consistency 整体关掉——
        混排毛笔体（主字体）与硬笔体（ZTi）会明显风格割裂。
        """
        if not self._variants_active:
            return None
        if ch.isascii() and ch.isalnum():
            return None
        n = self._char_seen.get(ch, 0)
        self._char_seen[ch] = n + 1
        cp = ord(ch)
        usable = []
        for p in self.variant_fonts:
            c = self._cmap(p)
            if c is None or cp in c:
                usable.append(p)
        if not usable:
            # 所有套都缺这个字：回退到全字表保底字体，否则会渲染出豆腐块。
            fb = self.symbol_font_path
            fc = self._cmap(fb)
            usable = [fb if (fc is None or cp in fc) else self.font_path]
        return usable[n % len(usable)]

    def _check_style_consistency(self, text, threshold=0.75):
        """风格一致性护栏：字库风格不符或覆盖率不足时关掉多套循环。

        两道检查，风格指纹在前（更根本）：
        1. **风格指纹**：校准只能统一色号/粗细/字号，改不了书写工具。
           毛笔体的笔画有粗细起伏、墨迹占比高；硬笔体是等宽线、墨迹占比低。
           实测马善政 vs ZTi：笔画宽只有 0.36–0.45 倍、墨迹占比 0.29–0.41 倍，
           放大+膨胀只会得到「变粗的硬笔线」，风格照样割裂。
        2. **覆盖率**：ZTi 只覆盖 137 个汉字，未覆盖的字回退到主字体，同样割裂。

        任何一项不通过就整体退回单套（保持风格统一）。
        """
        if len(self.variant_fonts) <= 1:
            self._variants_active = False
            self.style_note = "单套模式"
            return 0.0

        # 1) 风格指纹：与主字体不是同一种书写工具就拒绝混排
        for p in self.variant_fonts[1:]:
            ok, why = fontcal.style_compatible(self.font_path, p)
            if not ok:
                self._variants_active = False
                self.style_note = "%s %s，已退回单套" % (os.path.basename(p), why)
                return 0.0

        # 2) 覆盖率
        cov = fontcal.coverage(self.variant_fonts, text)
        self._variants_active = cov >= threshold
        self.style_note = ("多套循环（覆盖率 %.0f%%）" % (cov * 100)
                           if self._variants_active
                           else "覆盖率仅 %.0f%%，已退回单套" % (cov * 100))
        return cov

    def reset_variants(self):
        """清空同字多形出现计数（同一 Hand 渲染多篇独立文档时调用）。"""
        self._char_seen.clear()

    # 6.1 每个字的独立形变参数
    def _char_params(self, ch, px):
        r = self.rng
        j = self.jitter
        return dict(
            # 上下起伏要克制：±0.8px 时每个字像各写各的，收到 ±0.3px 更像同一支笔
            dy=r.uniform(-0.3, 0.3) * j * SCALE / 4.0,
            dx=r.uniform(-0.6, 0.6) * j * SCALE / 4.0,
            scale=1.0 + r.uniform(-0.035, 0.035) * j,
            # 单字旋转要小：±1.2° 叠在整行剪切上会让字看起来「东倒西歪」
            rot=r.uniform(-0.22, 0.22) * j,
            # 墨色深浅抖动收窄：220–255 时同段落里有字明显偏淡，看起来「粗细不统一」
            alpha=int(r.uniform(242, 255) if j > 0 else 250),
            rgb=tuple(max(0, min(255, c + r.randint(-3, 3) * (1 if j > 0 else 0)))
                      for c in self.ink),
            adv_jit=r.uniform(-0.12, 0.12) * j,                # em
            seed=r.randrange(1 << 30),                         # 供符号笔迹采样用
        )

    def _char_tile(self, ch, px, p, font_path=None):
        """生成单个字符的贴图，返回 (RGBA 图, ox, oy)。

        同字多形时不同字库的同一字符高度差很大（实测 13~31%），
        轮换起来像「忽大忽小」而不是「同字异形」。所以这里做**贴图闭环**：
        画完量墨迹高度，与目标高度比，按比例调字号重画（最多 3 轮）。
        这样只保留笔迹差异，大小严格统一。
        """
        base = font_path or self._font_for(ch)
        if _is_cjk(ch):                 # 只对汉字做缺字回退；标点/数字保持原路径
            base = self._with_fallback(ch, base)
        cal = dict(fontcal.params_for(base))
        is_cjk = _is_cjk(ch)
        # 主字体自身可能有 size_scale（手写汉字库的字面只有 0.64 em，要放大到与
        # 旧默认马善政体同样的观感大小）。量目标高度必须用「放大后」的字号，
        # 否则闭环会把数字/符号的目标压小一圈。
        main_scale = fontcal.params_for(self.font_path).get("size_scale", 1.0) or 1.0
        # 两个参照字号，别混：
        # · ref_cjk_px：主字体**实际渲染**的字号（汉字要和主字体自己的字高比）；
        # · ref_px：旧默认（马善政，size_scale=1）的字号。数字/符号/标点的目标
        #   高度一直是在这个字号下量的，换成新手写汉字后不能跟着放大 1.38 倍，
        #   否则数字会比以前大一圈。
        ref_cjk_px = max(4, int(round(px * main_scale)))
        ref_px = max(4, int(round(px)))

        # 目标高度：基准字体在参照字号下这个字的墨迹高。
        # 汉字 ×1.0；数字/符号 ×0.86（中文手写里数字通常比汉字略小）；标点 ×1.0。
        target_h = None
        ratio = (1.0 if (is_cjk or ch in _BOTTOM_MARKS or _is_cjk_punct(ch))
                 else fontcal.ASCII_HEIGHT_RATIO)
        # 贴底标点：不做高度闭环、也不改字号，直接用符号字库自己的校准字号。
        # 闭环会把它放大并把底边抬离基线（句号会从底部浮到中间）；
        # 按「回退字体里同一个标点」的高度去缩放又会被老 ZTi 的小标点带偏，
        # 实测逗号会缩到 7px（正常 13–16px）。符号字库本来就是同一个人写的，
        # 大小直接信它自己的 fontcal 即可。
        if base != self.font_path and ch in _BOTTOM_MARKS:
            pass
        elif base != self.font_path:
            try:
                if is_cjk:
                    # 汉字：优先对齐主字体「同一个字」的高度；字表外的字主字体没有，
                    # 就对齐主字体汉字中位高（回退字体只补字形，大小仍跟主字体一致）。
                    tgt = fontcal.ink_height_at(self.font_path, ch, ref_cjk_px)
                    if not tgt:
                        tgt = self._cjk_ref_h(ref_cjk_px)
                else:
                    # 数字/符号：主字体（手写汉字库）通常不含这些字符，
                    # 依次退到回退字体，在**旧默认字号**下量「同一个字」的高度作基准。
                    # 中文标点/括号有专门的大小参照字体（旧默认字体）时优先用它，
                    # 否则括号会比接入前小 20%（缩到 42px，正常 52px）。
                    ref = (self.punct_ref_font if (_is_cjk_punct(ch)
                                                   and self.punct_ref_font)
                           else self._ref_font_for(ch))
                    tgt = (fontcal.ink_height_at(ref, ch, ref_px)
                           if ref is not None else None)
                if tgt:
                    target_h = tgt * ratio
                    cal["size_scale"] = cal["size_scale"] * fontcal.glyph_scale(base, ch)
            except Exception:
                target_h = None

        boost = 1.0
        tile = None
        ox = oy = 0.0
        k = 1.0
        for attempt in range(3):
            size = max(4, int(round(px * p["scale"] * boost * cal["size_scale"] * k)))
            tile, ox, oy = self._render_glyph_tile(ch, size, p, base, cal, px)
            if tile is None:
                return None, 0, 0
            if target_h is None or attempt == 2:
                break
            bb = tile.getbbox()
            if bb is None:
                break
            got = bb[3] - bb[1]
            if got <= 0:
                break
            ratio = target_h / got
            if abs(ratio - 1.0) <= 0.04:      # 差 4% 以内就够了
                break
            k *= ratio
            k = max(0.5, min(2.5, k))
        return tile, ox, oy

    def _render_glyph_tile(self, ch, size, p, base, cal, px):
        """按给定字号画一次贴图（不含量高闭环）。"""
        # 合并字体里的数学符号是印刷体轮廓，走「骨架 → 变宽笔道」重绘
        if handstroke.available(ch):
            sym_path = base
            c = self._cmap(base)
            if c is not None and ord(ch) not in c:
                sym_path = self.symbol_font_path
            tile, ox, oy = handstroke.tile(
                ch, size, self.ink + (p["alpha"],), sym_path,
                seed=p.get("seed", 0))
            if tile is not None:
                return fontcal.finish_tile(tile, ox, oy, cal)
        f = _ttf(base, size)
        try:
            l, t, r, b = f.getbbox(ch)
        except Exception:
            return None, 0, 0
        pad = _TILE_PAD
        w = max(1, r - l) + pad * 2
        h = max(1, b - t) + pad * 2
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.text((pad - l, pad - t), ch, font=f, fill=p["rgb"] + (p["alpha"],))
        if abs(p["rot"]) > 0.05:
            img = img.rotate(p["rot"], resample=Image.BICUBIC, expand=True)
        # 数字/拉丁/希腊：ZTi-Sym 的笔画比汉字略粗，这里收掉一点边
        if self.symbol_thin > 0 and _is_plain_symbol(ch):
            img = _thin_alpha_tile(img, self.symbol_thin)
        # 字形在「主字体 advance」内居中，避免字距忽紧忽松
        if cal["size_scale"] != 1.0:
            base_adv = _ttf(self.font_path, int(round(px))).getlength(ch)
            cur_adv = f.getlength(ch)
            if cur_adv > 0 and abs(cur_adv - base_adv) > 0.5:
                l = l + (base_adv - cur_adv) / 2.0
        # ox/oy 是「相对笔位」偏移，单位是**画布 px**：贴图是按渲染字号
        # （可能乘过 size_scale）画出来的位图，贴回画布时是 1:1 像素，
        # 不能再除以 size_scale（除过一次会让放大过的字库整体下沉：
        # 实测汉字下沉 13px，行距被吃掉）。垂直方向按**基线对齐**：
        # 主字体 ascent 与字形字体 ascent 的差要补回来，否则放大过的字库
        # 因为 ascender 高于主字体而浮起来/沉下去。
        asc_base = _ttf(self.font_path, max(4, int(round(px)))).getmetrics()[0]
        asc_font = f.getmetrics()[0]
        return fontcal.finish_tile(img, pad - l,
                                   pad - t + (asc_font - asc_base), cal)

    def _font_for(self, ch):
        """拉丁字母/数字用「正常人笔写」的字体，汉字用毛笔手写体。

        马善政楷书的拉丁小写是连笔刷体（`a` 带大尾巴、`g` 是单层圈），
        正常人写不出来；龙藏体的拉丁是规整的硬笔写法，更像日常手写。
        所以正文里 ASCII 字母数字走 math_font_path，CJK 仍走 font_path。

        汉字走手写字库时，字表外的字（入/总/部…）主字体没有，
        按 han_fallback_fonts 依次回退，避免渲染成空白。
        """
        if ch.isascii() and ch.isalnum():
            return self.math_font_path
        if _is_cjk(ch) and self.han_fallback_fonts:
            ref = self._ref_font_for(ch)
            if ref is not None:
                return ref
        return self.font_path

    def _ref_font_for(self, ch):
        """量目标字高/取字形用的基准字体：主字体有这个字就用主字体，
        没有就按回退链找到第一个有它的字体；全都没有返回 None。
        """
        c = self._cmap(self.font_path)
        if c is None or ord(ch) in c:
            return self.font_path
        for fb in self.han_fallback_fonts:
            fc = self._cmap(fb)
            if fc is None or ord(ch) in fc:
                return fb
        return None

    def _cjk_ref_h(self, px):
        """主字体在给定字号下的汉字基准墨迹高（中位，px）。

        用于缺字回退/变体字形对齐：手写汉字库不含拉丁/数字，
        量不到「同一个字」的高度，就用主字体汉字的中位高当目标。
        """
        key = max(4, int(round(px)))
        if key in self._cjk_ref_cache:
            return self._cjk_ref_cache[key]
        cm = self._cmap(self.font_path)
        vals = []
        for ch in _CJK_REF_SAMPLE:
            if cm is not None and ord(ch) not in cm:
                continue
            h = fontcal.ink_height_at(self.font_path, ch, key)
            if h:
                vals.append(h)
        val = sorted(vals)[len(vals) // 2] if vals else None
        self._cjk_ref_cache[key] = val
        return val

    # ------------------------------------------------------ 换行与标点悬挂
    def wrap(self, text, width_pt):
        """按「行尾参差最小」做换行（动态规划），而不是贪心塞满。

        贪心换行的毛病：遇到放不下的长 token（尤其公式）就整段提前断，
        行尾留下大片空白，还容易挤出只有两三个字的孤行——看起来很不自然。
        这里改为最小化「每行右侧空白量的平方和」，行宽自然趋于均匀，
        同时保留中文行首/行尾禁则。
        """
        px = self.size_pt * SCALE
        font = _ttf(self.font_path, int(round(px)))
        # 逐字扰动会把实际行宽撑大几个百分点，换行时按同一比例预留，
        # 否则渲染出来会越过列宽（旧版就是因此每行右侧被裁掉约三成）。
        max_px = width_pt * SCALE / (1.0 + _JITTER_INFLATE * self.jitter)
        lines = []
        for para in _paragraphs(text):
            if not para.strip():
                lines.append("")
                continue
            lines.extend(self._wrap_para(_tokenize(para), font, px, max_px))
        return lines

    def _wrap_para(self, tokens, font, px, max_px):
        """单段落的最优换行：cost = Σ(行尾空白)² + 拆词惩罚，末行空白按 0.25 权重。

        为什么要加拆词惩罚：只优化「行宽均匀」会排出很机械的结果——为了填满行，
        它会把「三相电路」「功率因数」这种固定说法从中间劈开，而真人手写到行尾
        宁可挤一点也会把词写完（用户反馈原话）。
        """
        n = len(tokens)
        if not n:
            return []
        widths = [self._token_width(tk, font, px) for tk in tokens]

        # 允许在 i 前断行的条件：不是行首禁则字符，且 i-1 不是行尾禁则字符
        def can_break(i):
            if i <= 0 or i >= n:
                return True
            tk = tokens[i]
            prev = tokens[i - 1]
            if tk and tk[0] in NO_LINE_START:
                return False
            if prev and prev[-1] in NO_LINE_END:
                return False
            return True

        # 拆词惩罚：若在 j 处断行会切断一个「词」，加一笔代价。
        # 力度要克制：罚太重会让所有行都不敢填满、右侧留一大片白
        # （用户反馈「右边留白太多，不要都挤到左边」）。
        # 取 max_px² 的 0.04–0.12 量级：能压住「三角形|联结」这种明显拆词，
        # 又不会为了躲拆词把每行都砍到 80% 以下。
        def break_penalty(j, slack_ratio=0.0):
            """拆词惩罚：只在「行已经比较满」时才生效。

            早先是无条件惩罚，结果为了不拆词，行写到 85% 就停手，
            右侧留一大片白（实测区域 575pt 时块宽只有 489pt，空 86pt）。
            现在加条件：行填充率 < 85% 时不罚——宁可拆一个词也要把行写满；
            行已经够满（≥85%）时才避免拆词，保留「哪怕挤一点也不拆」的手感。
            """
            if j <= 0 or j >= n:
                return 0.0
            if slack_ratio > 0.15:          # 行还空着，优先填满
                return 0.0
            left = "".join(tokens[max(0, j - 6):j])
            right = "".join(tokens[j:j + 6])
            for ph in _PHRASES:
                for cut in range(1, len(ph)):
                    if left.endswith(ph[:cut]) and right.startswith(ph[cut:]):
                        return max_px * max_px * (0.02 + 0.017 * len(ph))
            return 0.0

        INF = float("inf")
        best = [INF] * (n + 1)
        back = [0] * (n + 1)
        best[0] = 0.0
        for i in range(1, n + 1):
            w = 0.0
            for j in range(i - 1, -1, -1):
                w += widths[j]
                if w > max_px * 1.35:      # 再往前只会更宽，剪枝
                    break
                if not can_break(j):
                    continue
                if best[j] == INF:
                    continue
                slack = max_px - w
                # 超宽行重罚（但允许标点悬挂造成的轻微超出）
                if slack < 0:
                    cost = (slack * 6.0) ** 2 + 1e6
                else:
                    weight = 0.25 if i == n else 1.0   # 末行不追求填满
                    cost = weight * slack * slack
                # 拆词惩罚：行接近满时避免拆词；行太空则优先填满
                cost += break_penalty(j, (slack / max_px) if max_px else 0.0)
                total = best[j] + cost
                if total < best[i]:
                    best[i] = total
                    back[i] = j
        if best[n] == INF:                 # 兜底：放不下就退回贪心
            return self._wrap_greedy(tokens, font, px, max_px)
        cuts, i = [], n
        while i > 0:
            cuts.append(i)
            i = back[i]
        cuts.append(0)
        cuts.reverse()
        return ["".join(tokens[cuts[k]:cuts[k + 1]]) for k in range(len(cuts) - 1)]

    def _wrap_greedy(self, tokens, font, px, max_px):
        lines, cur = [], ""
        for tk in tokens:
            if cur and self._token_width(cur + tk, font, px) > max_px:
                lines.append(cur)
                cur = tk
            else:
                cur += tk
        if cur:
            lines.append(cur)
        return lines

    # -------------------------------------------------------- 公式支持
    def _advance(self, ch, px):
        """单字前进宽度（px）。拉丁字母/数字走 math_font_path，汉字走 font_path。

        必须和 _font_for / _char_tile 保持一致：
        · 字体要一致（正文 ASCII 用龙藏体渲染，若按马善政量宽度字距会错位）
        · 字号要一致（fontcal 会把某些字体放大 1.3–1.5 倍，advance 不跟着放大
          就会出现「数字挤在一起」——实测 380V 的 3 和 8 几乎粘连）
        """
        if _formula_ready(ch):
            try:
                return formula.measure(_tex_char(ch), px, self.font_path, self.ink,
                                       self.math_font_path)[0]
            except Exception:
                pass
        path = self._font_for(ch)
        # 缺字回退要与 _char_tile 一致：.notdef 只有 0.6em，按它量宽
        # 会让含未覆盖字的行宽被低估，渲染后越出列宽。（只处理汉字，标点/数字保持原路径）
        if _is_cjk(ch):
            path = self._with_fallback(ch, path)
        p = fontcal.params_for(path)
        scale = p.get("size_scale", 1.0)
        adv = _ttf(path, max(4, int(round(px * scale)))).getlength(ch)
        # adv_scale 只收字距、不改字形：手写汉字库按整格等宽（advance=1000），
        # 放大到马善政字高后字距会松得离谱，靠它把步进收回到正常字距。
        return adv * p.get("adv_scale", 1.0)

    def _advance_formula(self, ch, px):
        """字符走公式渲染时的前进宽度。"""
        try:
            return formula.measure(_tex_char(ch), px, self.font_path, self.ink,
                                   self.math_font_path)[0]
        except Exception:
            return self._advance(ch, px)

    def _advance_with(self, ch, px, font_path):
        """按**实际使用的那套字体**量前进宽度。

        多套字库模式下，同一字符每出现一次可能换一套字体，各套 advance 不同
        （新字库窄、主字体宽）。若一律按主字体量，纯文本数字会出现明显间隙
        （实测 380 渲染成 3 8 0），而公式里却紧凑——两边不一致。
        """
        if not font_path or os.path.abspath(font_path) == os.path.abspath(self.font_path):
            return self._advance(ch, px)
        p = fontcal.params_for(font_path)
        scale = p.get("size_scale", 1.0)
        # 逐字校准可能再调一点，advance 要跟着
        scale *= fontcal.glyph_scale(font_path, ch)
        adv = _ttf(font_path, max(4, int(round(px * scale)))).getlength(ch)
        return adv * p.get("adv_scale", 1.0)

    def _text_width(self, s, px):
        return sum(self._advance(ch, px) for ch in s)

    def _token_width(self, tk, font, px):
        """一个 token 的排版宽度：普通文本按字宽累加，$...$ 交给公式层量。"""
        if _is_formula(tk):
            disp = _is_display_formula(tk)
            body = tk[2:-2] if disp else tk[1:-1]
            return formula.measure(body, px, self.font_path, self.ink,
                                   self.math_font_path, display=disp)[0]
        return self._text_width(tk, px)

    # -------------------------------------------------------- 渲染一个块
    def _line_box(self, line, font, px):
        """返回一行所需的 (宽, 基线以上, 基线以下)，公式会撑高行框。"""
        asc, desc = font.getmetrics()
        w = 0.0
        for kind, val in _atoms(line):
            if kind in ("f", "d"):
                fw, fa, fd = formula.measure(val, px, self.font_path, self.ink,
                                             self.math_font_path,
                                             display=(kind == "d"))
                w += fw
                asc = max(asc, fa)
                desc = max(desc, fd)
            elif kind == "c" and _formula_ready(val):
                fw, fa, fd = formula.measure(_tex_char(val), px, self.font_path,
                                             self.ink, self.math_font_path)
                w += fw
                asc = max(asc, fa)
                desc = max(desc, fd)
            elif kind in ("^", "_"):
                # 上下标按缩小字号量宽；上标会抬高行框上沿
                spx = px * _SUP_SCALE
                w += sum(self._advance(c, spx) for c in val)
                if kind == "^":
                    asc = max(asc, asc * _SUP_SCALE + px * _SUP_RISE)
                else:
                    desc = max(desc, desc * _SUP_SCALE + px * _SUB_DROP)
            else:
                w += self._advance(val, px)
        return w, asc, desc

    def render_block(self, text, width_pt=None, size_pt=None, line_pt=None):
        """返回 (RGBA Image, 宽pt, 高pt)。line_pt 不传时按字号自动算（1.85 倍）。"""
        if size_pt:
            self.size_pt = size_pt
        width_pt = width_pt or (PAGE_RIGHT_PT - PAGE_LEFT_PT)
        # 风格一致性护栏：多套字库覆盖不到正文时关掉循环，避免毛笔/硬笔混排
        self._check_style_consistency(text)
        lines = self.wrap(text, width_pt)
        px = self.size_pt * SCALE
        font = _ttf(self.font_path, int(round(px)))
        # 行距跟随字号：DEFAULT_LINE_PT 是按 12.4pt 定的一对常量，
        # 字号调大后若仍用 23pt 行距，行框装不下字，上半截会被画布裁掉
        # （实测 24pt 以上墨迹顶到 y=0）。这里按「字号 × 1.85」动态算，
        # 显式传了 line_pt 则优先用它。
        if line_pt:
            base_lh = float(line_pt) * SCALE
        else:
            base_lh = self.size_pt * _LINE_HEIGHT_RATIO * SCALE

        # 逐行按自身高度排布，但对「超出基准的部分」打折：分式/根号行确实需要更多
        # 垂直空间，但完全按内容给（最高到基准的 1.7 倍）会让整段行距忽大忽小。
        # 基线固定在行框的 0.78 处，超高的公式向上探入上一行的空白带——
        # 真人写带分式的笔记正是这样，行距只是略松，不会出现大段空白。
        metrics = [self._line_box(line, font, px) for line in lines]
        heights, baselines = [], []
        for _w, asc, desc in metrics:
            need = asc + desc
            h_i = base_lh + max(0.0, need - base_lh) * _LINE_HEIGHT_DAMP
            h_i = max(base_lh, h_i)
            # 基线要同时满足：不低于行框 0.78 处（保证重心）、字的上伸不出画布。
            # 早先这里还夹了个 base_lh*0.78 的上限，字号一大就把基线压死，
            # 字顶穿画布——去掉上限，改成「至少要容下 asc」。
            b_i = int(max(h_i * 0.78, asc + 2))
            h_i = int(max(h_i, b_i + desc + 2))
            heights.append(h_i)
            baselines.append(b_i)

        # 行距微抖动：完全等距看起来像排版软件，真人写不出这么准的间距。
        # 幅度取 ±0.9px（≈±0.22pt），只破坏「机械等距」的观感，不影响阅读。
        gaps = [int(round(self.rng.uniform(-0.9, 0.9) * self.jitter))
                for _ in lines]

        # 画布先给足：估算宽度不可靠（逐字扰动让实际墨迹比估算宽 1–4pt，
        # 防重叠右推还会继续外扩）。所以渲染后检查「墨迹是否顶到画布右缘」，
        # 顶到就加宽重画——宁可多画一次，也不能把行末的字切掉半个。
        est = max([m[0] for m in metrics] + [width_pt * SCALE])
        W = int(math.ceil(est + 0.02 * px * 8 + 24))
        H = max(1, sum(heights) + sum(gaps))
        for _ in range(3):
            canvas = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            y = 0
            for i, line in enumerate(lines):
                if line:
                    canvas.alpha_composite(
                        self._render_line(line, font, px, baselines[i], W / SCALE,
                                          heights[i]),
                        (0, y))
                y += heights[i] + gaps[i]
            out = canvas.crop((0, 0, W, min(H, y)))
            bb = out.getbbox()
            if not bb or bb[2] < W - 1:      # 没顶到右缘，说明没被裁
                break
            W += 48                          # 顶到了，加宽重画
        if bb:
            # 右侧留 6px 白边：贴着画布边缘会被误看成「字被切了」
            out = out.crop((0, 0, min(out.width, bb[2] + 6), out.height))
        return out, out.size[0] / SCALE, out.size[1] / SCALE

    def _render_line(self, line, font, px, baseline, width_pt, line_h):
        W = int(width_pt * SCALE)
        lh = int(line_h)
        img = Image.new("RGBA", (W, lh), (0, 0, 0, 0))
        ascent = font.getmetrics()[0]
        # 每行起笔位置：极其微小的左右偏移（±1.2px≈±0.3pt），只为打散「每行左端齐平」的机械感。
        # 加 2px 基底偏移，避免负值时贴图左边被画布裁掉。
        x = 2.0 + self.rng.uniform(-1.2, 1.2) * self.jitter
        atoms = _atoms(line)
        n = max(1, len(atoms))
        right = -1e9
        for k, (kind, val) in enumerate(atoms):
            if kind in ("f", "d"):
                # 公式整体当一个原子：不可拆、不与前后字重叠。
                # 公式里的单字符也参与 4 套轮换（用首字符决定，整条公式同一套，
                # 否则一条式子里字体会跳来跳去）。
                disp = (kind == "d")
                first = next((c for c in val if not c.isspace()), "")
                used_f = (self._variant_for(first) if first else None) or self.math_font_path
                fw, _fa, _fd = formula.measure(val, px, self.font_path, self.ink,
                                               used_f, display=disp)
                if x < right:
                    x = right
                formula.draw(img, val, x, baseline, px, self.font_path, self.ink,
                             used_f, display=disp)
                right = x + fw
                x += fw
                continue
            if kind in ("^", "_"):
                # 上标 / 下标：整组按 0.6 倍字号渲染，基线抬高/压低
                spx = px * _SUP_SCALE
                shift = -px * _SUP_RISE if kind == "^" else px * _SUB_DROP
                for c in val:
                    p2 = self._char_params(c, spx)
                    tile2, ox2, oy2 = self._char_tile(c, spx, p2, self._variant_for(c))
                    if tile2 is None:
                        continue
                    bb2 = tile2.getbbox()
                    if bb2 is None:
                        continue
                    if x < right:
                        x = right
                    img.alpha_composite(
                        tile2,
                        (int(round(x - ox2)), int(round(baseline + shift - oy2))))
                    right = x - ox2 + bb2[2]
                    x += self._advance(c, spx) * p2["scale"]
                continue
            ch = val
            if _formula_ready(ch):
                # 走公式渲染：与 $...$ 同源，粗细墨色一致。
                # 关键：把轮换选中的那套字体传给公式层，否则公式固定用
                # math_font_path（pass2），4 套轮换等于没生效。
                tex = _tex_char(ch)
                used_f = self._variant_for(ch) or self.math_font_path
                fw, _fa, _fd = formula.measure(tex, px, self.font_path, self.ink,
                                               used_f)
                if x < right:
                    x = right
                formula.draw(img, tex, x, baseline, px, self.font_path, self.ink,
                             used_f)
                right = x + fw
                x += fw
                continue
            p = self._char_params(ch, px)
            used = self._variant_for(ch)
            tile, ox, oy = self._char_tile(ch, px, p, used)
            if tile is None:
                continue
            bb = tile.getbbox()          # 贴图内的真实墨迹范围（旋转后也算得准）
            if bb is None:
                continue
            # 6.2 字距渐变：行首略宽，行末略紧
            drift = (0.06 - 0.12 * (k / n)) * px * self.jitter
            # 字距按「实际贴图宽度」算，而不是字体 advance：
            # _char_tile 里有闭环放大（对齐字高），字体 advance 不含这部分，
            # 两者脱节就会出现纯文本数字间的空隙（380 渲染成 3 8 0）。
            # 贴图四周有 _TILE_PAD 的透明内边距，相邻字会各多出 pad，
            # 造成「3 8 0」那种空隙。字距里要扣掉这部分。
            tile_w = (bb[2] - bb[0]) - _TILE_PAD * 2
            base_adv = self._advance_with(ch, px, used) * p["scale"]
            adv = max(base_adv * 0.96, tile_w) + p["adv_jit"] * px + drift
            px_x = x + p["dx"]
            px_y = baseline - ascent + p["dy"]
            # ² ³ ° ′ ″ 这类字在字体里被压在基线上，抬起 0.38em 回到角标位置
            if ch in _RAISE_CHARS:
                px_y -= px * _RAISE_EM
            # 防重叠：按「墨迹」边缘判断。贴图四周有 _TILE_PAD 的透明边距，
            # 若把边距算进字距，每个字都会被推开一个 padding，行宽膨胀约 40%，
            # 长行会被画布右边界直接裁掉。这里用 bb 反推墨迹的绝对位置。
            ink_left = px_x - ox + bb[0]
            if ink_left < right:
                px_x += right - ink_left
            img.alpha_composite(tile, (int(round(px_x - ox)), int(round(px_y - oy))))
            right = px_x - ox + bb[2]
            x += adv
        # 6.2 整行低频弯曲 + 剪切
        img = _sine_warp(img, amp=1.5 * self.jitter, lam=300.0, phase=self.rng.uniform(0, 6.28))
        if abs(self.jitter) > 0:
            # 剪切系数 sh 就是倾斜角的正切：0.15 约 8.5°，整行会明显歪；
            # 收到 0.021（约 1.2°）只留一点自然的行倾，不破坏版面观感。
            sh = self.rng.uniform(-0.021, 0.021) * self.jitter
            img = img.transform(img.size, Image.AFFINE,
                                (1, sh, -sh * lh / 2, 0, 1, 0), resample=Image.BICUBIC)
        return img


# ---------------------------------------------------------------- 工具函数
def _is_formula(tk):
    """$...$ 或 $$...$$ 形式的 token 视为公式。"""
    return len(tk) >= 3 and tk[0] == "$" and tk[-1] == "$"


def _is_display_formula(tk):
    """$$...$$ 视为 display 大型运算式。"""
    return len(tk) >= 5 and tk.startswith("$$") and tk.endswith("$$")


def _atoms(line):
    """把一行拆成原子：

        ('c', 单字)    普通字
        ('f', latex)   $...$ 公式（整体，不可断行）
        ('^', 文本)    上标（^ 后面的整组）
        ('_', 文本)    下标（_ 后面的整组）

    上下标规则（照顾手写输入习惯，不是严格 LaTeX）：
      · ^{11} / _{ij} 花括号整组
      · ^11  后面连续的「同类字符」全部当上标——所以 3^11 是 3 的 11 次方，
        而不是 LaTeX 那样只取一位（用户反馈：只能渲染 1 位，3^11 变成 3¹1）
      · ^a / ^Ω 单字符
    """
    out, i, n = [], 0, len(line)
    while i < n:
        ch = line[i]
        if ch == "$":
            # $$...$$ = display 大型运算式（上下限堆叠、分式放大）
            if line.startswith("$$", i):
                j = line.find("$$", i + 2)
                if j > i:
                    out.append(("d", line[i + 2:j]))
                    i = j + 2
                    continue
            j = line.find("$", i + 1)
            if j > i:
                out.append(("f", line[i + 1:j]))
                i = j + 1
                continue
        if ch in "^_":
            i += 1
            if i >= n:
                break
            if line[i] == "{":                      # ^{...} 花括号整组
                depth, j = 1, i + 1
                while j < n and depth:
                    if line[j] == "{":
                        depth += 1
                    elif line[j] == "}":
                        depth -= 1
                    j += 1
                out.append((ch, line[i + 1:j - 1]))
                i = j
                continue
            # ^11 / ^abc：取连续的同类字符（数字连数字、字母连字母）
            j = i
            if line[j].isdigit():
                while j < n and line[j].isdigit():
                    j += 1
            elif line[j].isalpha():
                while j < n and line[j].isalpha() and not ("\u4e00" <= line[j] <= "\u9fff"):
                    j += 1
            else:
                j = i + 1
            out.append((ch, line[i:j]))
            i = j
            continue
        out.append(("c", ch))
        i += 1
    return out


def _paragraphs(text):
    """把文本切成段落：空行才是硬分段，单个换行按软换行处理（交给列宽决定断点）。

    手写排版里「换行」应当由行宽决定；源文本里的单个 \\n 往往只是编辑器折行，
    若当成硬换行会出现「一行只有两三个字」的短行，看起来很不自然。
    """
    out, buf = [], []
    for raw in text.split("\n"):
        if raw.strip():
            buf.append(raw.strip())
        else:
            if buf:
                out.append(_join_lines(buf))
                buf = []
            out.append("")
    if buf:
        out.append(_join_lines(buf))
    return out


def _join_lines(lines):
    """软换行拼接：中文之间直接相连，拉丁词之间补一个空格。"""
    s = lines[0]
    for nxt in lines[1:]:
        if not s or not nxt:
            s += nxt
            continue
        if s[-1].isascii() and s[-1].isalnum() and nxt[0].isascii() and nxt[0].isalnum():
            s += " " + nxt
        else:
            s += nxt
    return s


def _tokenize(s):
    """中文逐字，连续 ASCII / 数字串不拆（避免把 220 拆开）；$...$ 整体保留。"""
    out, buf, i, n = [], "", 0, len(s)
    while i < n:
        ch = s[i]
        if ch == "$":
            if s.startswith("$$", i):
                j = s.find("$$", i + 2)
                if j > i:
                    if buf:
                        out.append(buf)
                        buf = ""
                    out.append(s[i:j + 2])   # 含两端 $$
                    i = j + 2
                    continue
            j = s.find("$", i + 1)
            if j > i:
                if buf:
                    out.append(buf)
                    buf = ""
                out.append(s[i:j + 1])       # 含两端 $
                i = j + 1
                continue
        if ch.isascii() and (ch.isalnum() or ch in ".,+-=%/×√"):
            buf += ch
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(ch)
        i += 1
    if buf:
        out.append(buf)
    return out


def _shift_rows(img, dy):
    if dy == 0:
        return img
    out = Image.new("RGBA", img.size, (0, 0, 0, 0))
    out.alpha_composite(img, (0, -int(dy)))
    return out


def _sine_warp(img, amp=1.5, lam=300.0, phase=0.0):
    if abs(amp) < 0.05:
        return img
    W, H = img.size
    out = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for x in range(W):
        d = int(round(amp * math.sin(2 * math.pi * x / lam + phase)))
        out.alpha_composite(img.crop((x, 0, x + 1, H)), (x, d))
    return out


def render_pages(texts, font_path, page_top_pt, width_pt=None,
                 size_pt=DEFAULT_SIZE_PT, seed=20261118):
    """把若干段文本作为独立块竖向排布，返回 [(Image, top_pt, w_pt, h_pt), ...]。"""
    hand = Hand(font_path, size_pt=size_pt, seed=seed)
    out, y = [], page_top_pt
    for t in texts:
        img, w, h = hand.render_block(t, width_pt=width_pt)
        out.append((img, y, w, h))
        y += h + BLOCK_GAP_PT
    return out


def paste_into_pdf(src_pdf, out_pdf, blocks_by_page, left_pt=PAGE_LEFT_PT):
    """blocks_by_page: {page_index: [(png_path_or_Image, top_pt, w_pt, h_pt), ...]}"""
    import fitz
    doc = fitz.open(src_pdf)
    for pno, blocks in blocks_by_page.items():
        page = doc[pno]
        for item, top, w, h in blocks:
            rect = fitz.Rect(left_pt, top, left_pt + w, top + h)
            if isinstance(item, str):
                page.insert_image(rect, filename=item, overlay=True, keep_proportion=False)
            else:
                import io
                buf = io.BytesIO()
                item.save(buf, format="PNG")
                page.insert_image(rect, stream=buf.getvalue(), overlay=True, keep_proportion=False)
    doc.save(out_pdf, deflate=True, garbage=3)
    doc.close()
    return out_pdf
