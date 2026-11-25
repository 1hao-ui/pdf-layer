# -*- coding: utf-8 -*-
"""做题.py — 作业页自动划题 + AI 答案手写排版（headless 命令行，不弹任何窗口）。

一条命令跑完「导入作业页 → 自动划定题目/答题区域 → 把答案手写写进去」：

    # 1) 只分析版面（题目/答题区），存 layout.json
    py -3 做题.py analyze 作业.pdf
    py -3 做题.py analyze 作业.pdf --page all --out out/作业_layout.json --json

    # 2) 用已有 layout + 答案 JSON 渲染手写作答 PDF（原内容保留）
    py -3 做题.py answer 作业.pdf --layout out/作业_layout.json ^
        --answers answers.json --out out/手写作答.pdf --png out/预览.png

    # 3) analyze + answer 一步
    py -3 做题.py run 作业.pdf --answers answers.json --out out/手写作答.pdf

    # 4) 题目页没有留白：答案写到每页作业后新插的空白页
    py -3 做题.py answer 作业.pdf --layout layout.json --answers answers.json --blank-page
    py -3 做题.py solve 作业.pdf --blank-page --out out/手写作答.pdf

答案文件（answers.json）支持两种写法：
    {"1": "解：……", "2": "解：……"}          # 键 = 题号或 Q1 形式的 id
    [{"id": "Q1", "text": "解：……"}, ...]

扫描件没有文字层：题号用顺序号占位。若想写真实题号（如 4.2.1、4.2.2），
analyze/run 时加 ``--numbers "4.2.1,4.2.2,..."``，答案文件就能按真实题号匹配。

只依赖 Pillow / PyMuPDF / numpy / scipy（图片模式用），**不 import tkinter**，
可以在服务器/脚本里无界面调用。产物默认写到 ``源码/pdf-layer/out/``。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import worksheet  # noqa: E402

OUT_DIR = os.path.join(HERE, "out")
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif")


def _utf8_console():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def _default_out(source, suffix):
    stem = os.path.splitext(os.path.basename(str(source)))[0] or "worksheet"
    return os.path.join(OUT_DIR, stem + suffix)


def _load_json(path, what):
    p = os.path.abspath(os.path.expanduser(os.path.expandvars(path)))
    if not os.path.exists(p):
        raise FileNotFoundError("%s不存在：%s" % (what, p))
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise ValueError("%s不是合法 JSON：%s（%s）" % (what, p, e))


def _print_spec(spec, show_text=True):
    print(worksheet.format_spec(spec, show_text=show_text))


def _cmd_analyze(args):
    spec = worksheet.analyze(args.source, page=args.page,
                             dpi=args.dpi, numbers=args.numbers)
    _print_spec(spec, show_text=not args.json)
    if args.out:
        path = worksheet.save_spec(spec, args.out)
        print("\nlayout 已保存: %s" % path)
    if args.json:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
    total = sum(len(pg["questions"]) for pg in spec["pages"])
    if total == 0:
        print("\n[失败] 没有识别到题目", file=sys.stderr)
        return 1
    return 0


def _do_answer(source, spec, answers_path, out_pdf, out_png, blank_page=False):
    answers = _load_json(answers_path, "答案文件")
    mode = worksheet.ANSWER_MODE_BLANK if blank_page else None
    pdf_path, warnings, coverage = worksheet.render_answers(
        spec, answers, out_pdf=out_pdf, out_png=out_png, answer_mode=mode)
    print("\n作答结果：%d/%d 题有答案，整体覆盖率 %.0f%%"
          % (coverage["answered"], coverage["questions"],
             100.0 * coverage["coverage"]))
    if coverage.get("answer_mode") == worksheet.ANSWER_MODE_BLANK:
        print("答案位置：新插入的空白页（每页作业后）")
        for src, outs in (coverage.get("blank_pages") or {}).items():
            print("  第 %d 页作业 → 输出第 %s 页"
                  % (int(src) + 1, "、".join(str(int(x) + 1) for x in outs)))
    for qid, info in coverage["sizes"].items():
        print("  %-8s 字号 %.1fpt  行距 %.1fpt  估算 %d 行  （可用 %.0fpt）%s"
              % (qid, info["size_pt"], info["line_pt"], info["lines_est"],
                 info["avail_pt"], "" if info["fits"] else "  [放不下]"))
    if pdf_path:
        print("PDF: %s" % pdf_path)
    if out_png:
        print("PNG: %s" % out_png)
    if warnings:
        print("\n警告 %d 条：" % len(warnings))
        for w in warnings:
            print("  [warning] " + w)
    return 0


def _cmd_answer(args):
    spec = worksheet.load_spec(args.layout)
    source = args.source or spec.get("source") or ""
    if not source:
        raise ValueError("答案命令需要 source，或 layout 里带 source")
    # layout 里的 source 可能是相对路径：优先用命令行给的
    if args.source:
        spec["source"] = os.path.abspath(
            os.path.expanduser(os.path.expandvars(args.source)))
    out_pdf = args.out or _default_out(source, "_answered.pdf")
    return _do_answer(source, spec, args.answers, out_pdf, args.png,
                      blank_page=args.blank_page)


def _cmd_run(args):
    spec = worksheet.analyze(args.source, page=args.page,
                             dpi=args.dpi, numbers=args.numbers)
    total = sum(len(pg["questions"]) for pg in spec["pages"])
    _print_spec(spec, show_text=not args.json)
    if total == 0:
        print("\n[失败] 没有识别到题目", file=sys.stderr)
        return 1
    if args.layout_out:
        print("\nlayout 已保存: %s"
              % worksheet.save_spec(spec, args.layout_out))
    if args.json:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
    out_pdf = args.out or _default_out(args.source, "_answered.pdf")
    out_png = args.png or _default_out(args.source, "_answered.png")
    return _do_answer(args.source, spec, args.answers, out_pdf, out_png,
                      blank_page=args.blank_page)


def _cmd_solve(args):
    """自动做题：划题 → 调模型（llm_client）作答 → 手写作答 PDF。"""
    import tempfile

    import ai_prompt
    import llm_client

    source = os.path.abspath(os.path.expanduser(os.path.expandvars(args.source)))
    if not os.path.exists(source):
        print("找不到作业页：%s" % source)
        return 2
    spec = worksheet.analyze(source, page=args.page, dpi=args.dpi,
                             numbers=args.numbers)
    total = sum(len(pg["questions"]) for pg in spec["pages"])
    _print_spec(spec, show_text=False)
    if total == 0:
        print("\n[失败] 没有识别到题目", file=sys.stderr)
        return 1
    if args.layout_out:
        print("\nlayout 已保存: %s" % worksheet.save_spec(spec, args.layout_out))

    prompt_spec = spec
    if args.blank_page:
        prompt_spec = dict(spec)
        prompt_spec["answer_mode"] = worksheet.ANSWER_MODE_BLANK
    prompt = ai_prompt.worksheet_prompt(prompt_spec)
    if not llm_client.available():
        print("\n没有配置答题模型：设置 HANDWRITE_LLM_API_KEY（可选 "
              "HANDWRITE_LLM_BASE_URL / HANDWRITE_LLM_MODEL）后重试；")
        print("也可以把下面提示词交给外部 AI 作答，再 做题.py answer --answers：")
        print(prompt[:1200])
        return 2
    print("\n调模型作答中…（%s）" % llm_client.public_config()["model"])
    try:
        got = llm_client.solve_worksheet(prompt_spec, prompt, vision=args.vision)
    except llm_client.LlmError as e:
        print("模型调用失败：%s" % e)
        if getattr(e, "hint", None):
            print("提示：%s" % e.hint)
        return 3
    meta = got.get("meta") or {}
    print("模型返回 %d 题答案（%s ms，%s）"
          % (len(got["answers"]), meta.get("elapsed_ms"), meta.get("model")))

    fd, tmp = tempfile.mkstemp(prefix="_ai_answers_", suffix=".json")
    os.close(fd)
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(got["answers"], f, ensure_ascii=False, indent=2)
        out_pdf = args.out or _default_out(source, "_AI作答.pdf")
        code = _do_answer(source, spec, tmp, out_pdf, args.png,
                          blank_page=args.blank_page)
        if args.answers_out:
            shutil.copyfile(tmp, args.answers_out)
            print("答案 JSON: %s" % args.answers_out)
        return code
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def build_parser():
    p = argparse.ArgumentParser(
        prog="做题.py",
        description="作业页自动划题 + AI 答案手写排版（headless，无 GUI 窗口）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例：
  py -3 做题.py analyze 作业.pdf
  py -3 做题.py analyze 作业.pdf --out out/作业_layout.json --json
  py -3 做题.py answer 作业.pdf --layout out/作业_layout.json --answers answers.json ^
      --out out/手写作答.pdf --png out/预览.png
  py -3 做题.py run 作业.pdf --answers answers.json --out out/手写作答.pdf
  py -3 做题.py run 扫描作业.pdf --numbers "4.2.1,4.2.2,4.2.4" --answers answers.json

答案文件：{"1": "解：…", "2": "解：…"} 或 [{"id": "Q1", "text": "解：…"}]
文档：docs/做题接口.md
""")
    sub = p.add_subparsers(dest="cmd",
                           metavar="{analyze,answer,run,solve}")

    def add_common(sp, with_page=True):
        sp.add_argument("source", help="作业页路径（PDF 或图片：PNG/JPG/WEBP/BMP/TIFF）")
        if with_page:
            sp.add_argument("--page", default="all",
                            help="只分析 PDF 的某一页（从 0 开始）或 all，默认 all")
            sp.add_argument("--dpi", type=float, default=None,
                            help="扫描件/图片 dpi 估计（默认图片元数据或 200）")
            sp.add_argument("--numbers", default=None,
                            help='按顺序指定真实题号，逗号分隔：'
                                 '"4.2.1,4.2.2,4.2.4"（扫描件无法 OCR 时用）')

    sp_a = sub.add_parser("analyze", help="只分析版面：题目/答题区 → layout.json",
                          formatter_class=argparse.RawDescriptionHelpFormatter,
                          description="导入作业页，自动划定题目区域与答题区域，"
                                      "输出 spec（layout.json）或直接打印。")
    add_common(sp_a)
    sp_a.add_argument("--out", default=None, help="layout JSON 输出路径")
    sp_a.add_argument("--json", action="store_true",
                      help="stdout 打印完整 spec JSON（含扫描件 crops）")
    sp_a.set_defaults(func=_cmd_analyze)

    sp_b = sub.add_parser("answer", help="按 layout + 答案 JSON 渲染手写作答 PDF",
                          formatter_class=argparse.RawDescriptionHelpFormatter,
                          description="读取 analyze 产出的 layout，把答案排进各题答题区，"
                                      "导出叠加原 PDF（原内容保留）与 PNG 预览。")
    sp_b.add_argument("source", nargs="?", default=None,
                      help="作业页路径（可省略，默认用 layout 里的 source）")
    sp_b.add_argument("--layout", required=True, help="analyze 产出的 layout.json")
    sp_b.add_argument("--answers", required=True, help="答案 JSON 文件")
    sp_b.add_argument("--out", default=None,
                      help="输出 PDF（默认 out/<作业名>_answered.pdf）")
    sp_b.add_argument("--png", default=None,
                      help="输出 PNG 预览（多页时只出第一个有答案的页）")
    sp_b.add_argument("--blank-page", action="store_true",
                      help="答案写到新插入的空白页（每页作业后插页），"
                           "不写在原页答题区；适合只有题目、没有留白的作业页")
    sp_b.set_defaults(func=_cmd_answer)

    sp_c = sub.add_parser("run", help="analyze + answer 一步完成",
                          formatter_class=argparse.RawDescriptionHelpFormatter,
                          description="导入作业页 → 自动划题 → 直接写答案导出，"
                                      "一步完成。")
    add_common(sp_c)
    sp_c.add_argument("--answers", required=True, help="答案 JSON 文件")
    sp_c.add_argument("--out", default=None,
                      help="输出 PDF（默认 out/<作业名>_answered.pdf）")
    sp_c.add_argument("--png", default=None,
                      help="输出 PNG 预览（默认 out/<作业名>_answered.png）")
    sp_c.add_argument("--layout-out", default=None,
                      help="顺便把 analyze 的 layout 存到该路径")
    sp_c.add_argument("--json", action="store_true", help="stdout 打印完整 spec JSON")
    sp_c.add_argument("--blank-page", action="store_true",
                      help="答案写到新插入的空白页（每页作业后插页）")
    sp_c.set_defaults(func=_cmd_run)

    sp_d = sub.add_parser("solve", help="自动做题：划题 → 调模型作答 → 手写作答 PDF",
                          description="需要先配置 HANDWRITE_LLM_API_KEY（OpenAI "
                                      "兼容端点，默认 DeepSeek）")
    sp_d.add_argument("source", help="作业页 PDF/图片（扫描件用 --numbers）")
    sp_d.add_argument("--page", type=int, default=None, help="只看第几页（0 起）")
    sp_d.add_argument("--numbers", default=None,
                      help="扫描件题号列表，逗号分隔，如 4.2.1,4.2.2")
    sp_d.add_argument("--dpi", type=int, default=None, help="扫描件渲染 DPI（默认自动）")
    sp_d.add_argument("--layout-out", default=None, help="把划题结果另存 layout JSON")
    sp_d.add_argument("--out", default=None, help="输出 PDF 路径")
    sp_d.add_argument("--png", default=None, help="同时输出第一页 PNG")
    sp_d.add_argument("--vision", type=int, default=None,
                      help="1/0：扫描件题干截图是否发给模型")
    sp_d.add_argument("--answers-out", default=None,
                      help="把模型给出的答案另存为 JSON（可手改后 answer）")
    sp_d.add_argument("--blank-page", action="store_true",
                      help="答案写到新插入的空白页（每页作业后插页）")
    sp_d.set_defaults(func=_cmd_solve)
    return p


def main(argv=None):
    _utf8_console()
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "cmd", None):
        parser.print_help()
        return 1
    try:
        return args.func(args)
    except (FileNotFoundError, ValueError) as e:
        print("[错误] %s" % e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已中断", file=sys.stderr)
        return 130
    except Exception as e:                    # noqa: BLE001
        print("[异常] %s: %s" % (type(e).__name__, e), file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
