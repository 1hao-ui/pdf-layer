# -*- coding: utf-8 -*-
"""symbols.py — 自建手写字库（ZTi-Sym 符号 + ZTi-Han 汉字）接入层。

背景
----
项目原先的数学符号是从 Segoe UI Symbol（印刷体）合并进中文手写字体的，
再用 `handstroke` 把轮廓骨架化成「笔道」。骨架化能去掉印刷感，但字形本身
仍是印刷体，而且骨架提取对 ∑ ∏ ∫ 这类复杂字形会失败（实测切成 160+ 段碎片）。

现在有两套**真人手写字库**，都来自同一个人的笔迹：
  · ZTi-Sym（204 字：数字/拉丁/希腊/运算符/集合逻辑/标点/单位），写了 4 遍；
    4 套按权重轮换（pass1 潦草降频），同字异形，且不再骨架化。
  · ZTi-Han（366 个汉字），写了 2 遍；pass2 为主字体、pass1 作同字异形。
汉字库覆盖不到的汉字，由 hwrender 的 `han_fallback_fonts` 按
「老 ZTi-Scan/otf → 龙藏 → 马善政」回退（按与 ZTi-Han 的风格接近程度排序），
不会留豆腐块。

用法
----
    from symbols import make_hand, make_han_hand
    # 汉字走中文手写字体，符号走 ZTi-Sym 4 套加权（老用法）
    hand = make_hand(size_pt=12.4, han_font="fonts/MaShanZheng-Regular-merged.ttf")
    # 汉字走 ZTi-Han 2 套，符号走 ZTi-Sym 4 套加权（推荐、GUI 默认）
    hand = make_han_hand(size_pt=12.4)
    img, w, h = hand.render_block("线电压380V，∑P=52kW", width_pt=404)
    print(han_coverage_summary("线电压380V，∑P=52kW"))

若字库目录不存在（例如只克隆了仓库、没拷字体输出目录），`make_hand` 和
`make_han_hand` 都会自动退回普通 `hwrender.Hand`，不会崩。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 打包成 exe 后（PyInstaller），数据目录在 sys._MEIPASS；字库随包放在
# _MEIPASS/字体输出 与 _MEIPASS/fonts 下，这里统一给出候选目录。
_FROZEN = bool(getattr(sys, "frozen", False))
_MEI = os.path.normpath(getattr(sys, "_MEIPASS", HERE)) if _FROZEN else None
# 符号字库可能的位置（按优先级）
SYM_DIR_CANDIDATES = ((
    os.environ.get("ZTISYM_DIR", ""),
    os.path.normpath(os.path.join(_MEI, "字体输出", "ZTi-Sym")),
    os.path.normpath(os.path.join(HERE, "fonts", "ZTi-Sym")),
) if _FROZEN else (
    os.environ.get("ZTISYM_DIR", ""),
    os.path.normpath(os.path.join(HERE, "..", "..", "字体输出", "ZTi-Sym")),
    os.path.normpath(os.path.join(HERE, "fonts", "ZTi-Sym")),
))
# 4 套权重（pass1 较潦草，降频；pass2 最清楚，主字体）
WEIGHTS = {1: 1, 2: 3, 3: 2, 4: 2}


def sym_dir():
    """返回可用的符号字库目录；找不到返回 None。"""
    for d in SYM_DIR_CANDIDATES:
        if d and os.path.isdir(d) and os.path.exists(os.path.join(d, "pass2.ttf")):
            return d
    return None


def available():
    return sym_dir() is not None


def _passes():
    d = sym_dir()
    if not d:
        return {}
    return {i: os.path.join(d, "pass%d.ttf" % i)
            for i in (1, 2, 3, 4)
            if os.path.exists(os.path.join(d, "pass%d.ttf" % i))}


# ---------------------------------------------------------------- 汉字字库
# 自建汉字字库（ZTi-Han，366 个汉字 × 2 遍）可能的位置（按优先级）
HAN_DIR_CANDIDATES = ((
    os.environ.get("ZTIHAN_DIR", ""),
    os.path.normpath(os.path.join(_MEI, "字体输出", "ZTi-Han")),
    os.path.normpath(os.path.join(HERE, "fonts", "ZTi-Han")),
) if _FROZEN else (
    os.environ.get("ZTIHAN_DIR", ""),
    os.path.normpath(os.path.join(HERE, "..", "..", "字体输出", "ZTi-Han")),
    os.path.normpath(os.path.join(HERE, "fonts", "ZTi-Han")),
))
# 汉字缺字回退：按「与 ZTi-Han 的书写风格接近程度」排序（style_fingerprint 实测
# han 笔画宽 6.0 / 墨迹占比 0.108）：
#   老 ZTi-Scan-01..04.ttf（0.83x / 0.71x，最像）→ 老 ZTi-01..04.otf
#   → 龙藏合并字体（1.33x / 1.45x）→ 马善政合并字体（1.83x / 1.86x，毛笔，最后兜底）
_OLD_ZTI_DIR = os.path.join(HERE, "fonts", "ZTi")
_LONGCANG_FONT = os.path.join(HERE, "fonts", "LongCang-Regular-merged.ttf")
_MASHAN_FONT = os.path.join(HERE, "fonts", "MaShanZheng-Regular-merged.ttf")
_CMAP_CACHE = {}


def han_dir():
    """返回可用的汉字字库目录；找不到返回 None。"""
    for d in HAN_DIR_CANDIDATES:
        if not d or not os.path.isdir(d):
            continue
        if any(os.path.exists(os.path.join(d, "pass%d.ttf" % i)) for i in (1, 2, 3, 4)):
            return d
    return None


def han_available():
    return han_main_font() is not None


def han_passes():
    """汉字字库的各遍：{1: pass1.ttf, 2: pass2.ttf, ...}（缺的遍不出现）。"""
    d = han_dir()
    if not d:
        return {}
    return {i: os.path.join(d, "pass%d.ttf" % i)
            for i in (1, 2, 3, 4)
            if os.path.exists(os.path.join(d, "pass%d.ttf" % i))}


def han_main_font():
    """汉字主字体：优先 pass2（最清楚），其次 pass1，再次任意存在的遍。"""
    passes = han_passes()
    if not passes:
        return None
    return passes.get(2) or passes.get(1) or passes[sorted(passes)[0]]


def _han_variant_fonts():
    """除主字体外的汉字变体（例如 pass1），同字异形用。"""
    main = han_main_font()
    if not main:
        return []
    return [p for i, p in sorted(han_passes().items())
            if os.path.abspath(p) != os.path.abspath(main)]


def _han_fallback_fonts():
    """汉字缺字回退链（顺序 = 与 ZTi-Han 的风格接近程度）。

    老 ZTi-Scan-01..04.ttf → 老 ZTi-01..04.otf → 龙藏合并字体 → 马善政合并字体。
    注：老 ZTi 的 137 字全部包含在 366 字表内，对覆盖率补 0；真正补缺字的
    是龙藏/马善政，但顺序仍按风格排（链只用于主字体缺字时取字形）。
    """
    out = []
    if os.path.isdir(_OLD_ZTI_DIR):
        names = [n for n in os.listdir(_OLD_ZTI_DIR)
                 if n.lower().endswith((".otf", ".ttf"))]
        # 扫描件（ZTi-Scan，最像）在前，其次 otf；各自按名字排序
        for n in sorted(names, key=lambda n: (0 if "scan" in n.lower() else 1, n)):
            p = os.path.join(_OLD_ZTI_DIR, n)
            if os.path.exists(p):
                out.append(p)
    for p in (_LONGCANG_FONT, _MASHAN_FONT):
        if os.path.exists(p):
            out.append(p)
    return out


def _font_cmap(path):
    """字体 cmap（Unicode 覆盖集合）；读不到返回 None。"""
    if not path:
        return None
    key = os.path.abspath(path)
    if key not in _CMAP_CACHE:
        try:
            from fontTools.ttLib import TTFont
            _CMAP_CACHE[key] = set(TTFont(path).getBestCmap() or {})
        except Exception:
            _CMAP_CACHE[key] = None
    return _CMAP_CACHE[key]


def _hand_takes_param(H, name):
    """hwrender.Hand 是否接受某个关键字参数（兼容旧版引擎）。"""
    import inspect
    try:
        params = inspect.signature(H.Hand.__init__).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind == p.VAR_KEYWORD for p in params.values())


def _hand_takes_han_fallback(H):
    """hwrender.Hand 是否已支持 han_fallback_fonts 参数（兼容旧版引擎）。"""
    return _hand_takes_param(H, "han_fallback_fonts")


def _is_cjk(ch):
    return "\u4e00" <= ch <= "\u9fff"


def make_hand(han_font, size_pt=None, seed=20261118, weights=None,
              jitter=1.0, ink=None, math_font=None, **kwargs):
    """构造「汉字用手写中文字体 + 符号用 ZTi-Sym 4 套加权」的 Hand。

    han_font  中文手写字体路径（汉字与公式里的中文用它）
    math_font 公式层字体；不传则用 ZTi-Sym pass2（符号也是手写）
    """
    import hwrender as H

    passes = _passes()
    if not passes:
        return H.Hand(han_font, size_pt=size_pt or H.DEFAULT_SIZE_PT,
                      seed=seed, jitter=jitter, ink=ink or H.INK, **kwargs)

    kw = dict(size_pt=size_pt or H.DEFAULT_SIZE_PT, seed=seed,
              jitter=jitter, ink=ink or H.INK)
    kw.setdefault("math_font_path", math_font or passes.get(2))
    kw.setdefault("symbol_font_path", passes.get(2))
    kw.update(kwargs)

    main = passes.get(2)
    variants = [p for i, p in passes.items() if i != 2]

    class _SymHand(H.Hand):
        """汉字不参与轮换；数字/拉丁/希腊/符号按权重轮换。"""

        def __init__(self):
            super().__init__(han_font, variant_fonts=variants, **kw)
            w = dict(WEIGHTS if weights is None else weights)
            self._pool = []
            for i in (2, 3, 4, 1):
                if i in passes:
                    self._pool += [passes[i]] * max(0, int(w.get(i, 0)))
            if not self._pool:
                self._pool = [main]
            self._variants_active = True
            self.style_note = "符号 ZTi-Sym 4 套加权（pass1 1/8）"

        def _check_style_consistency(self, text, threshold=0.75):
            # 4 套是同一个人写的，风格一致；这里不做覆盖率回退，
            # 否则正文里混汉字时会把符号轮换整体关掉。
            self._variants_active = True
            return 1.0

        def _variant_for(self, ch):
            if not self._variants_active or ch.isspace():
                return None
            if "\u4e00" <= ch <= "\u9fff":      # 汉字交给 han_font
                return None
            # 按权重**随机**挑一套，而不是 n % len 的确定性循环。
            # 循环的问题：pass2,pass2,pass2,pass3,pass3,pass4,pass4,pass1 严格重复，
            # 同一个字连续出现时字形变化是周期性的，看起来像「没随机」。
            n = self._char_seen.get(ch, 0)
            self._char_seen[ch] = n + 1
            cp = ord(ch)
            usable = [p for p in self._pool
                      if (c := self._cmap(p)) is None or cp in c]
            if not usable:
                return None
            if len(usable) == 1:
                return usable[0]
            return self.rng.choice(usable)

    return _SymHand()


# ---------------------------------------------------------------- 汉字手写体
def make_han_hand(size_pt=None, seed=20261118, jitter=1.0, ink=None,
                  weights=None, han_fallback=True, **kwargs):
    """构造「汉字 ZTi-Han 2 套 + 符号 ZTi-Sym 4 套加权」的全手写 Hand。

    参数
    ----
    size_pt    字号 pt；不传则用 hwrender.DEFAULT_SIZE_PT
    seed/jitter/ink  引擎参数（同 hwrender.Hand）
    weights    ZTi-Sym 4 套权重，默认 {1:1, 2:3, 3:2, 4:2}
    han_fallback
               True 时把「老 ZTi-Scan/otf → 龙藏 → 马善政合并字体」作为
               `han_fallback_fonts` 传给引擎：汉字主字体缺字时按链回退
               （顺序按与 ZTi-Han 的风格接近程度排，马善政字最全、最后兜底）。
    preserve_strokes（可选，经 **kwargs 传入）
               True（默认）关闭 handstroke 符号二次笔迹化，保留 ZTi-Sym 原笔迹。
    """
    import hwrender as H

    # 保留原笔迹：符号是真人手写的，再走「骨架→笔道」会把笔锋糊掉。
    # import 放函数内，避免 symbols <-> handstroke 的循环导入。
    preserve = kwargs.pop("preserve_strokes", True)
    if preserve:
        try:
            import handstroke
            handstroke.STROKE_SYMBOLS = set()
        except Exception:
            pass

    main = han_main_font()
    if not main:
        # 没有自建汉字库：退回「马善政/老 ZTi + ZTi-Sym」的老路径，不能崩。
        olds = [p for p in ([_MASHAN_FONT] + _han_fallback_fonts())
                if os.path.exists(p)]
        if olds:
            return make_hand(olds[0], size_pt=size_pt, seed=seed, jitter=jitter,
                             ink=ink, weights=weights, **kwargs)
        return H.Hand(_MASHAN_FONT, size_pt=size_pt or H.DEFAULT_SIZE_PT,
                      seed=seed, jitter=jitter, ink=ink or H.INK, **kwargs)

    han_variants = _han_variant_fonts()
    passes = _passes()
    sym_main = passes.get(2) or (passes[sorted(passes)[0]] if passes else None)

    kw = dict(size_pt=size_pt or H.DEFAULT_SIZE_PT, seed=seed,
              jitter=jitter, ink=ink or H.INK)
    if sym_main:
        kw.setdefault("math_font_path", sym_main)
        kw.setdefault("symbol_font_path", sym_main)
    kw.update(kwargs)
    if han_fallback:
        fb = _han_fallback_fonts()
        if fb and _hand_takes_han_fallback(H):
            kw.setdefault("han_fallback_fonts", fb)
    # 中文标点/括号的大小参照：用接入前的旧默认字体（马善政），
    # 否则括号会按回退字体变小一圈（实测 52px → 42px）。
    if _hand_takes_param(H, "punct_ref_font") and os.path.exists(_MASHAN_FONT):
        kw.setdefault("punct_ref_font", _MASHAN_FONT)

    w = dict(WEIGHTS if weights is None else weights)
    pool = []
    for i in (2, 3, 4, 1):
        if i in passes:
            pool += [passes[i]] * max(0, int(w.get(i, 0)))
    if not pool:
        # ZTi-Sym 不在时：数字/符号退回字体最全的合并字体（不轮换）
        alt = next((p for p in ([_MASHAN_FONT] + _han_fallback_fonts())
                    if os.path.exists(p)), None)
        if alt:
            kw.setdefault("math_font_path", alt)
            kw.setdefault("symbol_font_path", alt)
    n_han = 1 + len(han_variants)
    n_sym = len({os.path.abspath(p) for p in pool})

    class _HanHand(H.Hand):
        """汉字在 ZTi-Han 两套里轮换；非汉字按权重用 ZTi-Sym 4 套。"""

        def __init__(self):
            super().__init__(main, variant_fonts=han_variants, **kw)
            self._sym_pool = list(pool)
            self._variants_active = True
            if n_sym:
                self.style_note = ("汉字 ZTi-Han %d 套 + 符号 ZTi-Sym %d 套加权"
                                   % (n_han, n_sym))
            else:
                self.style_note = "汉字 ZTi-Han %d 套（符号库缺失，已回退）" % n_han

        def _check_style_consistency(self, text, threshold=0.75):
            # 汉字、符号都出自同一人的笔迹，风格天然一致；不做覆盖率回退，
            # 否则正文里汉字覆盖不全就会把符号轮换整体关掉。
            self._variants_active = True
            return 1.0

        def _advance(self, ch, px):
            # 空格不画字，只参与折行宽度估算。ZTi-Han/ZTi-Sym 都没有 space
            # 字形，PIL 缺 space 时按 1em 返回，带空格的串会折行过早；
            # 这里按「中文体正常空格」≈0.25em 算（旧默认马善政是 0.22em）。
            if ch.isspace():
                return px * 0.25
            return super()._advance(ch, px)

        def _variant_for(self, ch):
            if ch.isspace() or not self._variants_active:
                return None
            n = self._char_seen.get(ch, 0)
            self._char_seen[ch] = n + 1
            cp = ord(ch)
            if _is_cjk(ch):
                # 汉字只从 ZTi-Han 的各遍里按出现次数轮换；
                # 两套都缺这个字时返回 None，交给 han_fallback_fonts 回退。
                usable = [p for p in self.variant_fonts
                          if (c := self._cmap(p)) is None or cp in c]
                if not usable:
                    return None
                return usable[n % len(usable)]
            # 数字/拉丁/希腊/符号：按权重随机挑一套（不是 n % len 的周期性循环）
            usable = [p for p in self._sym_pool
                      if (c := self._cmap(p)) is None or cp in c]
            if not usable:
                # ZTi-Sym 4 套都不含这个字符（典型：全角 ＝＋－～）：
                # 依次用汉字回退链里的字库补，避免渲染成空白。
                usable = [p for p in getattr(self, "han_fallback_fonts", [])
                          if (c := self._cmap(p)) is None or cp in c]
                if not usable:
                    return None
                return usable[0]
            if len(usable) == 1:
                return usable[0]
            return self.rng.choice(usable)

    return _HanHand()


def han_coverage_summary(text):
    """统计 text 里的汉字由 ZTi-Han 主字体覆盖的比例（短字符串，供 GUI 显示）。

    例："汉字覆盖 90%（29/32，缺 入总部 回退）"——缺字会由 han_fallback_fonts
    回退到老 ZTi / 马善政，所以不是空白，但需要提示用户哪些字不在自建字库里。
    """
    main = han_main_font()
    if not main:
        return "汉字库 ZTi-Han 缺失，已退回老字体"
    chars = [c for c in (text or "") if _is_cjk(c)]
    if not chars:
        return "汉字覆盖 —（无汉字）"
    cm = _font_cmap(main)
    if cm is None:
        return "汉字覆盖 未知（主字体 cmap 读取失败）"
    hit = sum(1 for c in chars if ord(c) in cm)
    miss = []
    for c in chars:
        if ord(c) not in cm and c not in miss:
            miss.append(c)
    s = "汉字覆盖 %.0f%%（%d/%d" % (100.0 * hit / len(chars), hit, len(chars))
    if miss:
        shown = "".join(miss[:12]) + ("…" if len(miss) > 12 else "")
        s += "，缺 %s 回退" % shown
    return s + "）"


if __name__ == "__main__":
    import sys
    print("符号字库目录:", sym_dir() or "未找到")
    if not available():
        sys.exit(1)
    from PIL import Image
    hand = make_hand(os.path.join(HERE, "fonts", "MaShanZheng-Regular-merged.ttf"))
    print("Hand:", hand.style_note)
    txt = "线电压380V，√3≈1.732，∑P=52.1kW，θ=30°，η=95%，25℃"
    img, w, h = hand.render_block(txt, width_pt=404)
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    out = os.path.join(HERE, "out", "symbols_demo.png")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bg.convert("RGB").save(out)
    print("块 %.1f x %.1f pt -> %s" % (w, h, out))
