# -*- coding: utf-8 -*-
"""symbols.py — 人手写符号字库（ZTi-Sym）接入层。

背景
----
项目原先的数学符号是从 Segoe UI Symbol（印刷体）合并进中文手写字体的，
再用 `handstroke` 把轮廓骨架化成「笔道」。骨架化能去掉印刷感，但字形本身
仍是印刷体，而且骨架提取对 ∑ ∏ ∫ 这类复杂字形会失败（实测切成 160+ 段碎片）。

现在有一套**真人手写的符号字库**（ZTi-Sym，204 字：数字/拉丁/希腊/运算符/
集合逻辑/标点/单位），同一人写了 4 遍，正好可以：
  · 数字、拉丁、希腊、符号直接用真笔迹，不再骨架化
  · 4 套按权重轮换（pass1 潦草降频），同字异形
  · 汉字仍走中文手写字体（ZTi-Sym 不含汉字）

用法
----
    from symbols import make_hand
    hand = make_hand(size_pt=12.4, han_font="fonts/MaShanZheng-Regular-merged.ttf")
    img, w, h = hand.render_block("线电压380V，∑P=52kW", width_pt=404)

若符号字库不存在（例如只克隆了仓库、没拷字体输出目录），`make_hand` 会
自动退回普通 `hwrender.Hand`，不会崩。
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
# 符号字库可能的位置（按优先级）
SYM_DIR_CANDIDATES = (
    os.environ.get("ZTISYM_DIR", ""),
    os.path.normpath(os.path.join(HERE, "..", "..", "字体输出", "ZTi-Sym")),
    os.path.normpath(os.path.join(HERE, "fonts", "ZTi-Sym")),
)
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
            n = self._char_seen.get(ch, 0)
            self._char_seen[ch] = n + 1
            cp = ord(ch)
            usable = [p for p in self._pool
                      if (c := self._cmap(p)) is None or cp in c]
            if not usable:
                return None
            return usable[n % len(usable)]

    return _SymHand()


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
