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
    # 完整希腊字母表：缺字会回退到系统印刷字体，公式里混进印刷字形（如 α 变成细体 a）
    "αβγδεζηικλμνξοπρστυχψ"
    "ΓΔΘΛΞΠΥΦΨ"
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


# ---------------------------------------------------------------- 字形替换
# 有些字符字体里本来就有，但写法不符合「正常人写字」。典型是小写 a：
# 龙藏体/楷体都写成双层 a（印刷体的那种，带顶钩），而日常手写是单层 ɑ
# （就是 @ 里面那个 a）。这里用 Ink Free（Windows 自带手写体）的单层 a 覆盖掉。
REPLACE_FONT = r"C:\Windows\Fonts\inkfree.ttf"
REPLACE_CPS = {0x61: "小写 a 用单层 ɑ"}


def replace_glyphs(cn_path, src_path, out_path, cps):
    """把 cps 里的码位用 src_path 的字形覆盖（不是新增，是替换）。"""
    dst = TTFont(cn_path)
    src = TTFont(src_path, fontNumber=0)
    src_cmap = src.getBestCmap()
    src_gs = src.getGlyphSet()
    upem_src = src["head"].unitsPerEm
    upem_dst = dst["head"].unitsPerEm
    scale = upem_dst / float(upem_src)

    glyf = dst["glyf"]
    hmtx = dst["hmtx"]
    order = list(dst.getGlyphOrder())
    dst_cmap = dst.getBestCmap()
    done = []
    for cp in cps:
        gname = src_cmap.get(cp)
        if not gname:
            continue
        target = dst_cmap.get(cp)
        if target is None:
            target = "uni%04X" % cp
            order.append(target)
        rp = DecomposingRecordingPen(src_gs)
        src_gs[gname].draw(rp)
        pen = TTGlyphPen(None)
        from fontTools.pens.transformPen import TransformPen
        rp.replay(TransformPen(pen, (scale, 0, 0, scale, 0, 0)))
        g = pen.glyph()
        glyf.glyphs[target] = g
        adv = int(round(src["hmtx"][gname][0] * scale))
        hmtx.metrics[target] = (adv, getattr(g, "xMin", 0) or 0)
        for tb in dst["cmap"].tables:
            if tb.format in (4, 12):
                try:
                    tb.cmap[cp] = target
                except Exception:
                    pass
        done.append(chr(cp))
    dst.setGlyphOrder(order)
    dst.save(out_path)
    return done


def main():
    d = os.path.dirname(os.path.abspath(__file__))
    if len(sys.argv) == 4:
        jobs = [(sys.argv[1], sys.argv[2], sys.argv[3])]
    else:
        jobs = []
        # 与 fetch_fonts.ps1 的下载列表保持一致（四款都生成 merged 版）。
        # 注：交接说明 §3 认为刘建毛草连笔过重（x 易看成 p），不建议作正文；
        #     这里仍生成 merged 版，只为保留可选项，默认正文仍用马善政。
        for fam in ["MaShanZheng-Regular", "LongCang-Regular",
                    "LiuJianMaoCao-Regular", "ZhiMangXing-Regular"]:
            jobs.append((os.path.join(d, "fonts", fam + ".ttf"), DEFAULT_SYM,
                         os.path.join(d, "fonts", fam + "-merged.ttf")))
    for cn, sym, out in jobs:
        try:
            a = merge(cn, sym, out)
            print("%-30s merged %2d glyphs -> %s (%d bytes)"
                  % (os.path.basename(cn), len(a), os.path.basename(out), os.path.getsize(out)))
            # 替换不符合日常写法的字形（单层 a）
            if os.path.exists(REPLACE_FONT):
                rep = replace_glyphs(out, REPLACE_FONT, out, REPLACE_CPS)
                print("%-30s replaced %d glyphs: %r"
                      % ("", len(rep), "".join(rep)))
            else:
                print("%-30s 跳过字形替换（缺 %s）" % ("", REPLACE_FONT))
        except Exception as e:
            print("%-30s FAIL %r" % (os.path.basename(cn), e))


if __name__ == "__main__":
    main()
