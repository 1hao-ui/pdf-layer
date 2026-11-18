# -*- coding: utf-8 -*-
"""merge_symbols.py

把符号字体（Segoe UI Symbol）里的希腊字母 / 数学符号字形注入中文手写字体，
输出一个自包含的合并字体，彻底消除「中文手写 + 符号印刷体」的混排观感差。

用法:
    python merge_symbols.py                # 处理 fonts/ 下的三款中文字体
    python merge_symbols.py CN.ttf SYM.ttf OUT.ttf
"""
import os
import sys

from fontTools.ttLib import TTFont
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.pens.recordingPen import DecomposingRecordingPen

SYMS = [ord(c) for c in (
    "Ωφω√≈∠²³·×−°θΔΣπ±≤≥≠∞→∘⌀"      # 初版：希腊 + 常见运算符
    "∑∫∏∂∇∈⊂∪∩∀∃←⇒⇔⊥∥⟨⟩⌈⌉⌊⌋⋯⋮⁄"      # 公式需要：大运算符 / 集合 / 箭头 / 括号件
)]
DEFAULT_SYM = r"C:\Windows\Fonts\seguisym.ttf"


def covered(font):
    s = set()
    for tb in font["cmap"].tables:
        s |= set(tb.cmap.keys())
    return s


def merge(cn_path, sym_path, out_path, extra=()):
    dst = TTFont(cn_path)
    src = TTFont(sym_path, fontNumber=0)

    have = covered(dst)
    src_cmap = src.getBestCmap()
    src_gs = src.getGlyphSet()

    glyf = dst["glyf"]
    hmtx = dst["hmtx"]
    upem_src = src["head"].unitsPerEm
    upem_dst = dst["head"].unitsPerEm
    scale = upem_dst / float(upem_src)

    order = list(dst.getGlyphOrder())
    added = []

    for cp in list(SYMS) + list(extra):
        if cp in have:
            continue
        gname = src_cmap.get(cp)
        if not gname:
            continue
        new = "uni%04X" % cp
        if new in glyf.glyphs:
            continue

        # 递归展开复合字形，只留下纯轮廓
        rp = DecomposingRecordingPen(src_gs)
        src_gs[gname].draw(rp)
        pen = TTGlyphPen(None)
        if scale != 1.0:
            from fontTools.pens.transformPen import TransformPen
            rp.replay(TransformPen(pen, (scale, 0, 0, scale, 0, 0)))
        else:
            rp.replay(pen)
        g = pen.glyph()

        glyf.glyphs[new] = g
        order.append(new)
        adv = src["hmtx"][gname][0] * scale
        hmtx.metrics[new] = (int(round(adv)), getattr(g, "xMin", 0) or 0)

        for tb in dst["cmap"].tables:
            if tb.format in (4, 12):
                try:
                    tb.cmap[cp] = new
                except Exception:
                    pass
        added.append(new)

    dst.setGlyphOrder(order)
    dst.save(out_path)
    return added


def main():
    d = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) == 4:
        jobs = [(sys.argv[1], sys.argv[2], sys.argv[3])]
    else:
        jobs = []
        for fam in ["MaShanZheng-Regular", "LongCang-Regular", "ZhiMangXing-Regular"]:
            jobs.append((os.path.join(d, "fonts", fam + ".ttf"), DEFAULT_SYM,
                         os.path.join(d, "fonts", fam + "-merged.ttf")))
    for cn, sym, out in jobs:
        try:
            a = merge(cn, sym, out)
            print("%-30s merged %2d glyphs -> %s (%d bytes)"
                  % (os.path.basename(cn), len(a), os.path.basename(out), os.path.getsize(out)))
        except Exception as e:
            print("%-30s FAIL %r" % (os.path.basename(cn), e))


if __name__ == "__main__":
    main()
