# -*- coding: utf-8 -*-
"""worksheet_regression.py — 做题链路的轻量回归（不依赖模型、不弹窗口）。

覆盖三件用户实际反馈过的事：
1. 数字版 PDF 的插图要参与划区：答题区不能盖在图上；
2. 题目页没有留白时，answer_mode=blank 会插空白答题页，一题太长会续页；
3. 默认 inline 导出仍然是「原页叠加」，页数不变、原内容保留。

用法（在 源码/pdf-layer 下）：
    py -3 tests/worksheet_regression.py
退出码 0 = 全过；失败会打印断言信息。
"""
from __future__ import annotations

import io
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pymupdf as fitz                       # noqa: E402
from PIL import Image, ImageDraw             # noqa: E402

import worksheet                             # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "worksheet", "作业A.pdf")


def _make_question_with_figure(path):
    """造一页「题干 + 插图 + 下一题」的数字版 PDF。"""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((56, 110), "1. Look at the figure and answer:",
                     fontname="china-s", fontsize=12)
    page.insert_text((56, 132), "what is the current?", fontname="china-s",
                     fontsize=12)
    img = Image.new("RGB", (300, 200), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([8, 8, 292, 192], outline=(0, 0, 0), width=2)
    d.line([8, 100, 292, 100], fill=(0, 0, 0), width=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    figure_rect = fitz.Rect(56, 160, 356, 360)
    page.insert_image(figure_rect, stream=buf.getvalue())
    page.insert_text((56, 430), "2. Explain the result.", fontname="china-s",
                     fontsize=12)
    doc.save(path)
    doc.close()
    return figure_rect


def test_figure_aware_regions(tmp):
    src = os.path.join(tmp, "figure.pdf")
    fig = _make_question_with_figure(src)
    spec = worksheet.analyze(src)
    qs = [q for pg in spec["pages"] for q in pg["questions"]]
    assert len(qs) == 2, "应识别 2 题，实际 %d" % len(qs)
    q1, q2 = qs
    # 题干下方的插图要并进题目区
    assert q1["rect"][3] >= fig.y1 - 1.0, \
        "题目区没有包含插图：%r vs 图底 %.1f" % (q1["rect"], fig.y1)
    # 答题区不能与插图相交，也不能压到下一题
    assert q1["answer_rect"][1] >= fig.y1 - 1.0, \
        "答题区起点落在插图内：%r" % (q1["answer_rect"],)
    assert q1["answer_rect"][3] <= q2["rect"][1] + 0.5, \
        "答题区越过下一题：%r" % (q1["answer_rect"],)
    return spec


def test_blank_answer_pages(tmp, spec):
    answers = {
        "1": "解：由图可知电流为2A。",
        "2": "解：结果如上。",
    }
    out = os.path.join(tmp, "blank.pdf")
    pdf, warns, cov = worksheet.render_answers(
        spec, answers, out_pdf=out, answer_mode=worksheet.ANSWER_MODE_BLANK)
    assert os.path.exists(pdf)
    assert cov["answer_mode"] == "blank"
    blank = cov.get("blank_pages") or {}
    assert blank, "blank_pages 映射为空"
    src_count = fitz.open(spec["source"]).page_count
    out_count = fitz.open(pdf).page_count
    assert out_count == src_count + sum(len(v) for v in blank.values()), \
        "空白答题页数量不对：%d -> %d，映射 %r" % (src_count, out_count, blank)
    # 源页原样保留（第一张作业页的文本仍可提取）
    doc = fitz.open(pdf)
    try:
        assert "1." in doc[0].get_text()
    finally:
        doc.close()
    return pdf


def test_blank_answer_paper_and_ink(tmp, spec):
    """空白答题页可选纸张尺寸/材质，笔色能写进 PDF。"""
    import numpy as np
    out = os.path.join(tmp, "blank_style.pdf")
    pdf, _warns, cov = worksheet.render_answers(
        spec, {"1": "解：由图可知电流为2A。"}, out_pdf=out,
        answer_mode=worksheet.ANSWER_MODE_BLANK, ink=(170, 42, 46),
        paper={"size": "B5", "landscape": False, "material": "kraft"})
    blank = cov.get("blank_pages") or {}
    assert blank, "没有生成空白答题页"
    page_idx = blank[min(blank)][0]
    doc = fitz.open(pdf)
    try:
        pg = doc[page_idx]
        assert abs(pg.rect.width - 498.9) < 0.6, pg.rect
        assert abs(pg.rect.height - 708.7) < 0.6, pg.rect
        pix = pg.get_pixmap(dpi=60)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)[:, :, :3].astype(np.int16)
        corner = arr[8, 8]
        assert corner[0] > corner[1] > corner[2], "牛皮纸底色不对：%r" % (corner,)
        red = (arr[:, :, 0] - arr[:, :, 1] > 80).sum()
        assert red > 30, "红笔笔迹没有写进 PDF（红色像素 %d）" % red
    finally:
        doc.close()


def test_long_answer_continues(tmp, spec):
    """一题特别长时应自动续页，而不是截断或报错。"""
    long_text = "解：" + ("这是很长的答案，用来验证空白页续页。" * 80)
    out = os.path.join(tmp, "long.pdf")
    pdf, warns, cov = worksheet.render_answers(
        spec, {"1": long_text}, out_pdf=out,
        answer_mode=worksheet.ANSWER_MODE_BLANK)
    blank = cov.get("blank_pages") or {}
    assert len(blank.get(0, [])) >= 2, \
        "长答案没有续页：%r" % (cov.get("sizes", {}).get("Q1"),)
    assert os.path.exists(pdf)


def test_inline_keeps_page_count(tmp, spec):
    answers = {"1": "解：短答案。", "2": "解：另一题。"}
    out = os.path.join(tmp, "inline.pdf")
    pdf, _w, cov = worksheet.render_answers(spec, answers, out_pdf=out)
    assert cov["answer_mode"] == "inline"
    assert fitz.open(pdf).page_count == fitz.open(spec["source"]).page_count


def test_prompt_blank_budget(spec):
    """空白答题页模式下，提示词不应再被原页 1 行答题区限制。"""
    import ai_prompt
    inline = ai_prompt._worksheet_question_lines(spec)
    blank_spec = dict(spec)
    blank_spec["answer_mode"] = worksheet.ANSWER_MODE_BLANK
    blank = ai_prompt._worksheet_question_lines(blank_spec)
    assert all(b["lines"] > i["lines"] for i, b in zip(inline, blank)), \
        "空白页提示词预算没有放大：%r -> %r" % (inline[0], blank[0])


def test_humanize_math():
    """斜杠 → 分数式、裸根号 → \\sqrt{} 的归一化（用户实测反馈的 4 类坑）。"""
    cases = [
        # 普通数字除法
        ("解：I=220/10=22A。", "解：I=$\\frac{220}{10}$=22A。"),
        # 幂曾被吞进分母：U^2/R 不能变成 U^\frac{2}{R}
        ("P=U^2/R", "P=$\\frac{U^2}{R}$"),
        ("U^{2}/R", "$\\frac{U^{2}}{R}$"),
        # 链式除法只转第一段 → 现在折成一个分数，分母用 \times
        ("I=380/1.732/10", "I=$\\frac{380}{1.732\\times 10}$"),
        # 多字符分母
        ("S=P/cosφ", "S=$\\frac{P}{cosφ}$"),
        # 括号分母（用户截图里的实际写法，2026-11-26 补）
        ("I1=P1/(Ucosφ)", "I1=$\\frac{P1}{Ucosφ}$"),
        ("I=400/(220×0.5)", "I=$\\frac{400}{220×0.5}$"),
        ("I2=P/(√3×380×0.8)", "I2=$\\frac{P}{\\sqrt{3}×380×0.8}$"),
        # 裸根号：被开方数必须被包进根号
        ("U/√3", "$\\frac{U}{\\sqrt{3}}$"),
        ("√3×220", "$\\sqrt{3}$×220"),
        ("√(a+b)", "$\\sqrt{a+b}$"),
        # 已经在 $...$ 里的：只换成 \frac，不重复加定界符
        ("$I=U/R$", "$I=\\frac{U}{R}$"),
        ("$220/10$", "$\\frac{220}{10}$"),
        # 紧贴汉字的除法/根号也要认（\w 在 Python 里包含汉字，不能用 \w 做边界）
        ("约3/4的电压", "约$\\frac{3}{4}$的电压"),
        ("I=220/10伏", "I=$\\frac{220}{10}$伏"),
        ("约√3倍", "约$\\sqrt{3}$倍"),
        # 数字+单位的比值不动
        ("50/60Hz", "50/60Hz"),
        # 单位比值不是分数
        ("kvar/h", "kvar/h"),
        ("kW/h", "kW/h"),
        ("220V/10A", "220V/10A"),
    ]
    for src, want in cases:
        got = worksheet.humanize_answer(src)
        assert got == want, \
            "humanize_answer(%r) = %r，期望 %r" % (src, got, want)
    for src, _want in cases:
        once = worksheet.humanize_answer(src)
        assert worksheet.humanize_answer(once) == once, \
            "humanize_answer 不幂等：%r -> %r" % (src, once)


def test_charset_dynamic():
    """字表跟着当前字库走：扩展 A 也算汉字，汉字/符号表不重叠。"""
    import ai_prompt
    assert ai_prompt.is_han_char("相")
    assert ai_prompt.is_han_char("㐀"), "扩展 A 应算汉字（字库工作台可选）"
    assert ai_prompt.is_han_char("〇") and ai_prompt.is_han_char("々")
    assert not ai_prompt.is_han_char("A")
    assert not ai_prompt.is_han_char("√")
    han, sym = ai_prompt.allowed_charset()
    assert han and sym, "字表为空：检查字体输出/ 下的字库"
    assert not (han & sym), "汉字表与符号表不应重叠"
    assert "A" in sym and "A" not in han


def test_charset_cache_invalidation(tmp):
    """原地重建同名字体后，字表缓存必须自动失效（否则要重启程序）。"""
    import shutil
    import time

    import ai_prompt
    src = ai_prompt.han_font_path()
    if not src:
        return
    tmp_font = os.path.join(tmp, "pass2.ttf")
    shutil.copyfile(src, tmp_font)
    real_han, real_sym = ai_prompt.han_font_path, ai_prompt.symbol_font_path
    try:
        ai_prompt.han_font_path = lambda: tmp_font
        ai_prompt.symbol_font_path = lambda: None
        ai_prompt.reload_charset()
        n1 = len(ai_prompt.allowed_charset()[0])
        sig1 = ai_prompt._font_signature()
        os.utime(tmp_font, (time.time() + 5, time.time() + 5))
        n2 = len(ai_prompt.allowed_charset()[0])
        sig2 = ai_prompt._font_signature()
        assert sig1 != sig2, "字体被覆盖后指纹没变，缓存不会失效"
        assert n1 == n2, "换文件后字表内容异常：%d -> %d" % (n1, n2)
    finally:
        ai_prompt.han_font_path = real_han
        ai_prompt.symbol_font_path = real_sym
        ai_prompt.reload_charset()


def test_answer_height_estimate():
    """高度估算要和渲染器实测对齐（纯文字 1 行距 / 分数按墨迹加高）。

    2026-11-26 起估算不再用「行数 × 行距 + 补偿」，而是直接走
    hwrender.Hand.measure_block（真实换行 + 真实公式盒），所以：
      · 单行纯文字 = 1 个行距（22.9pt）；
      · 含分式的一行 = 墨迹高（约 31–33pt，分子分母各占一段）；
      · 根号行比纯文字略高（radical 的上横与下勾）——旧断言的「根号不增高」
        是按字数估的时代的结论，现在按墨迹算，允许 3pt 内的增加。
    """
    size = 12.4
    lp = round(size * worksheet.LINE_RATIO, 1)
    plain = "解：电流为22安。"
    frac = r"解：电流 $I=\frac{220}{10}=22A$。"
    sqrt_t = r"解：电压 $U=\sqrt{3}\times 220=381V$。"
    h_plain = worksheet.estimate_answer_height(plain, 400, size, lp)
    h_frac = worksheet.estimate_answer_height(frac, 400, size, lp)
    h_sqrt = worksheet.estimate_answer_height(sqrt_t, 400, size, lp)
    assert abs(h_plain - 22.9) < 0.5, "纯文字一行应≈22.9pt，实际 %.1f" % h_plain
    assert 29.0 <= h_frac <= 34.0, "分数一行应≈31.3pt，实际 %.1f" % h_frac
    assert 0.0 <= h_sqrt - h_plain <= 3.0, \
        "根号只应比纯文字高一点（radical 上横/下勾）：%.1f vs %.1f" % (h_sqrt, h_plain)
    # 宽度：分数按排版算，不再按字符数（旧算法约 9.4 字宽）
    assert 1.5 < worksheet._em_units(r"$\frac{220}{10}$") < 2.6


def main():
    with tempfile.TemporaryDirectory(prefix="worksheet_reg_") as tmp:
        spec = worksheet.analyze(FIXTURE, numbers="4.2.1,4.2.2")
        total = sum(len(pg["questions"]) for pg in spec["pages"])
        assert total == 4, "真实样本应识别 4 题，实际 %d" % total
        qs = [q for pg in spec["pages"] for q in pg["questions"]]
        for q in qs:
            ar = q["answer_rect"]
            assert ar[0] < ar[2] and ar[1] < ar[3], "答题区非法：%r" % (ar,)
        print("[ok] fixture analyze：4 题，答题区合法")
        test_humanize_math()
        print("[ok] 斜杠/根号归一化：分数式、幂、链式除法、单位比值、裸根号")
        test_charset_dynamic()
        print("[ok] 字表动态：扩展 A 算汉字、汉字/符号表不重叠")
        test_charset_cache_invalidation(tmp)
        print("[ok] 字表缓存：字体文件一改就失效")
        test_answer_height_estimate()
        print("[ok] 高度估算：分数 31.3pt / 纯文字 22.9pt 与实测一致")
        test_prompt_blank_budget(spec)
        print("[ok] 空白页提示词：可写字数预算按整页放大")

        fig_spec = test_figure_aware_regions(tmp)
        print("[ok] 插图参与划区：题目区含图、答题区避让")
        test_blank_answer_pages(tmp, fig_spec)
        print("[ok] 空白答题页：插入顺序与页数正确")
        test_blank_answer_paper_and_ink(tmp, fig_spec)
        print("[ok] 空白答题页：纸张尺寸/材质 + 笔色写进 PDF")
        test_long_answer_continues(tmp, fig_spec)
        print("[ok] 长答案：自动续页")
        test_inline_keeps_page_count(tmp, fig_spec)
        print("[ok] inline：页数不变")
    print("全部通过")


if __name__ == "__main__":
    main()
