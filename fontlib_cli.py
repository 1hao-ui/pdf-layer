# -*- coding: utf-8 -*-
"""fontlib_cli.py — 自建字库的命令行入口（界面之外的第二条路）。

常用
----
    # 看现有字库
    py -3 fontlib_cli.py list

    # 生成「补当前缺字」的书写模板（2 遍，11.3mm 格）
    py -3 fontlib_cli.py template --preset missing --passes 2 --out out\\补字模板

    # 写完/扫描后：建工程 → 识别 → 自动参数 → 建库并启用
    py -3 fontlib_cli.py new --id my-hand --name 我的补字库 --preset missing
    py -3 fontlib_cli.py images --id my-hand 扫描\\*.jpg
    py -3 fontlib_cli.py analyze --id my-hand
    py -3 fontlib_cli.py build --id my-hand --merge-from ziti-han

    # 全链路自检（不需要打印机）
    py -3 fontlib_cli.py selftest
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from fontlib import charsets, registry, session, template          # noqa: E402


def _expand(patterns):
    out = []
    for p in patterns or []:
        hits = sorted(glob.glob(p))
        out.extend(hits or ([p] if os.path.exists(p) else []))
    return out


def _active_fonts(kind="han"):
    lib = registry.active(kind)
    return registry.pass_paths(lib) if lib else []


def _chars_from_args(args, exclude_default=False):
    preset = getattr(args, "preset", "custom")
    fonts = _active_fonts("han") if (
        preset == "missing" or getattr(args, "exclude_existing", False)
        or exclude_default) else []
    return charsets.resolve(
        preset, text=getattr(args, "text", "") or "",
        path=getattr(args, "file", "") or "", font_paths=fonts,
        exclude_existing=getattr(args, "exclude_existing", False),
        drop_rare=not getattr(args, "include_rare", False))


# ---------------------------------------------------------------- 命令
def cmd_list(args):
    data = registry.ensure()
    print("注册表：", registry.registry_path())
    print("当前：汉字=%s  符号=%s" % (
        (data.get("active") or {}).get("han"),
        (data.get("active") or {}).get("sym")))
    print("%-18s %-6s %6s %-6s %s" % ("id", "类型", "字数", "状态", "名称"))
    for lib in data["libraries"]:
        print("%-18s %-6s %6d %-6s %s" % (
            lib["id"], lib["kind"], lib["char_count"],
            "缺文件" if lib.get("missing") else ("启用" if lib["enabled"] else "停用"),
            lib["name"]))
    return 0


def cmd_charset(args):
    chars = _chars_from_args(args)
    print(charsets.describe(chars))
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        charsets.save_list(chars, args.out)
        print("字表 ->", args.out)
    else:
        print("".join(chars[:80]), "..." if len(chars) > 80 else "")
    return 0


def cmd_template(args):
    chars = _chars_from_args(args)
    if not chars:
        print("字表为空：检查 --preset/--text/--file")
        return 2
    est = template.estimate(chars, cell_pt=template.cell_pt_from_mm(args.cell_mm),
                            passes=args.passes)
    files = template.save_bundle(
        chars, args.out, passes=args.passes,
        cell_pt=template.cell_pt_from_mm(args.cell_mm),
        title=args.title, stem=args.stem,
        show_reference=not args.no_reference,
        progress=lambda i, n: print("  画第 %d/%d 页" % (i, n), end="\r"))
    print()
    print("字表 %d 字，每页 %d 格，共 %d 页（%d 遍）"
          % (est["count"], est["per_page"], est["total_pages"], est["passes"]))
    print("PDF    ->", files["pdf"])
    print("layout ->", files["layout"])
    print("字表   ->", files["chars_txt"])
    return 0


def _session(args, create=False):
    root = args.work_root or session.DEFAULT_ROOT
    work = os.path.join(root, args.id)
    if create:
        chars = _chars_from_args(args)
        if not chars:
            raise SystemExit("字表为空：检查 --preset/--text/--file")
        s = session.Session.create(
            work, args.id, args.name or args.id, chars,
            passes=args.passes, cell_pt=template.cell_pt_from_mm(args.cell_mm),
            preset=getattr(args, "preset", "custom"))
        print("工程 ->", work, "（%d 字）" % len(chars))
        return s
    if os.path.isdir(work) and os.path.exists(os.path.join(work, "session.json")):
        return session.Session.load(work)
    s = session.Session.open_existing(args.id)
    if s is None:
        raise SystemExit("找不到工程：%s（先跑 new）" % work)
    return s


def cmd_new(args):
    s = _session(args, create=True)
    files = s.make_template(passes=args.passes)
    print("模板 ->", files["pdf"])
    return 0


def cmd_images(args):
    s = _session(args)
    imgs = _expand(args.images)
    if not imgs:
        print("没有匹配到图片")
        return 2
    s.add_images(imgs)
    print("已登记 %d 张图：" % len(imgs))
    for p in imgs:
        print("  ", os.path.basename(p))
    return 0


def cmd_analyze(args):
    s = _session(args)
    rep = s.analyze(threshold=args.threshold,
                    auto_align=not args.no_align,
                    progress=lambda i, n: print("  识别 %d/%d" % (i, n), end="\r"))
    print()
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    sug = s.suggest()
    print("自动参数：", json.dumps(sug["params"], ensure_ascii=False))
    if sug["warnings"]:
        print("需要复核 %d 个字：" % len(sug["warnings"]))
        for ch, why in list(sug["warnings"].items())[:20]:
            print("   %s  %s" % (ch, "；".join(why)))
    return 0


def cmd_build(args):
    s = _session(args)
    meta = s.build(merge_from=args.merge_from, activate=not args.no_activate,
                   progress=lambda i, n, note: print("  第 %d/%d 遍" % (i, n)))
    print("字库 ->", meta["target_dir"])
    print("字数 %d，遍数 %d" % (meta["char_count"], len(meta["passes_abs"])))
    for p, v in meta["calibration"].items():
        print("  %s  size_scale=%.4f  adv_scale=%.4f"
              % (os.path.basename(p), v["size_scale"], v["adv_scale"]))
    if meta.get("preview"):
        print("字形总览 ->", meta["preview"])
    if not args.no_activate:
        print("已设为当前汉字库（渲染立即生效）")
    return 0


def cmd_preview(args):
    s = _session(args)
    p = s.preview(text=args.text or None, size_pt=args.size)
    print("效果示例 ->", p)
    return 0


def cmd_import(args):
    from fontlib import importer
    paths = _expand(args.files)
    if not paths:
        print("没有匹配到字体文件")
        return 2
    m = importer.import_fonts(paths, lib_id=args.id or None,
                              name=args.name or None,
                              activate=not args.no_activate)
    print("已导入「%s」→ %s" % (m["name"], m["target_dir"]))
    print("  %d 字（%s）" % (m["char_count"],
                             "汉字库" if m["kind"] == "han" else "符号库"))
    for p, v in m.get("calibration", {}).items():
        print("  %s  size_scale=%.4f  adv_scale=%.4f"
              % (p, v["size_scale"], v["adv_scale"]))
    if m.get("preview"):
        print("  字形总览 ->", m["preview"])
    return 0


def cmd_activate(args):
    registry.set_active("han", args.id)
    print("当前汉字库 =", args.id)
    return 0


def cmd_selftest(args):
    from fontlib import selftest
    rep = selftest.run(work_dir=args.work_dir, passes=args.passes)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


# ---------------------------------------------------------------- CLI
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="自建字库：选字 → 模板 → 识别 → 微调 → 建库")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add_charset(p):
        p.add_argument("--preset", default="custom",
                       choices=["missing", "gb2312-l1", "gb2312", "cjk",
                                "custom"])
        p.add_argument("--text", default="", help="自定义正文")
        p.add_argument("--file", default="", help="自定义字表/语料文件")
        p.add_argument("--exclude-existing", action="store_true",
                       help="去掉当前字库已有的字")
        p.add_argument("--include-rare", action="store_true",
                       help="包含 GB2312 之外的生僻字")

    p = sub.add_parser("list", help="列出已登记字库")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("charset", help="预览/导出字表")
    add_charset(p)
    p.add_argument("--out", default="")
    p.set_defaults(func=cmd_charset)

    p = sub.add_parser("template", help="生成书写模板")
    add_charset(p)
    p.add_argument("--out", default=os.path.join("out", "fontlab", "模板"))
    p.add_argument("--passes", type=int, default=2)
    p.add_argument("--cell-mm", type=float, default=11.3)
    p.add_argument("--title", default="手写模板")
    p.add_argument("--stem", default="模板")
    p.add_argument("--no-reference", action="store_true")
    p.set_defaults(func=cmd_template)

    p = sub.add_parser("new", help="新建工程并出模板")
    add_charset(p)
    p.add_argument("--id", required=True)
    p.add_argument("--name", default="")
    p.add_argument("--passes", type=int, default=2)
    p.add_argument("--cell-mm", type=float, default=11.3)
    p.add_argument("--work-root", default="")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("images", help="登记扫描图/PDF")
    p.add_argument("--id", required=True)
    p.add_argument("images", nargs="+")
    p.add_argument("--work-root", default="")
    p.set_defaults(func=cmd_images)

    p = sub.add_parser("analyze", help="自动识别、切字、给参数")
    p.add_argument("--id", required=True)
    p.add_argument("--threshold", default="auto")
    p.add_argument("--no-align", action="store_true")
    p.add_argument("--work-root", default="")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("build", help="建库 + 校准 +（可选）启用")
    p.add_argument("--id", required=True)
    p.add_argument("--merge-from", default="",
                   help="把现有字库并进来（补字），如 ziti-han")
    p.add_argument("--no-activate", action="store_true")
    p.add_argument("--work-root", default="")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("preview", help="效果示例")
    p.add_argument("--id", required=True)
    p.add_argument("--text", default="")
    p.add_argument("--size", type=float, default=12.4)
    p.add_argument("--work-root", default="")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("import-font", help="上传现成字体文件（TTF/OTF/TTC）")
    p.add_argument("files", nargs="+",
                   help="字体文件，按主→副顺序（多个 = 多遍）")
    p.add_argument("--id", default="", help="字库 id / 目录名")
    p.add_argument("--name", default="", help="显示名")
    p.add_argument("--no-activate", action="store_true")
    p.set_defaults(func=cmd_import)

    p = sub.add_parser("activate", help="切换当前汉字库")
    p.add_argument("id")
    p.set_defaults(func=cmd_activate)

    p = sub.add_parser("selftest", help="无头全链路自检")
    p.add_argument("--work-dir", default="")
    p.add_argument("--passes", type=int, default=2)
    p.set_defaults(func=cmd_selftest)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
