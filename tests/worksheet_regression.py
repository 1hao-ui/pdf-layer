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
        test_prompt_blank_budget(spec)
        print("[ok] 空白页提示词：可写字数预算按整页放大")

        fig_spec = test_figure_aware_regions(tmp)
        print("[ok] 插图参与划区：题目区含图、答题区避让")
        test_blank_answer_pages(tmp, fig_spec)
        print("[ok] 空白答题页：插入顺序与页数正确")
        test_long_answer_continues(tmp, fig_spec)
        print("[ok] 长答案：自动续页")
        test_inline_keeps_page_count(tmp, fig_spec)
        print("[ok] inline：页数不变")
    print("全部通过")


if __name__ == "__main__":
    main()
