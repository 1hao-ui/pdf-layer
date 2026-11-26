# -*- coding: utf-8 -*-
"""tests/test_zw_ui.py — 做题窗口（M7）主界面的无头回归。

跑法（在 源码/pdf-layer 下）：
    py -3 tests\\test_zw_ui.py

只验界面结构，不跑识别/AI/生成：窗口能构、顶栏是一行、图标/沙滩条在、
菜单与日志抽屉能开合、编辑区控件齐全。没有显示环境时 SKIP。
"""
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import theme  # noqa: E402


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS  " if ok else "FAIL  ") + name
          + (("   [" + str(detail) + "]") if detail else ""))


def managed(w):
    try:
        return bool(w.winfo_manager())
    except Exception:
        return False


def main():
    if not theme.available():
        print("SKIP 没有可用的 tkinter/显示，跳过做题窗口界面回归")
        return 0
    import tkinter as tk
    import 做题窗口 as zw

    root = tk.Tk()
    root.withdraw()
    app = None
    try:
        app = zw.WorksheetWindow(root)
        root.update()
        check("窗口构建成功", True)

        # ---- 顶栏 ----
        check("顶栏只有一行（高度 < 90）", app.toolbar.winfo_reqheight() < 90,
              app.toolbar.winfo_reqheight())
        for name in ("btn_ai", "btn_generate", "page_entry", "page_total_label",
                     "log_text", "text_editor", "size_spin", "align_box"):
            check("控件存在：" + name, getattr(app, name, None) is not None)
        check("题号输入收进弹窗（顶栏不再占位）",
              not hasattr(app, "numbers_entry") or app.numbers_entry is None)
        check("更多菜单锚点存在", app._more_anchor is not None)
        try:
            orient = str(app.right_paned.cget("orient"))
        except Exception:
            orient = "?"
        check("预览/答案编辑是横向分栏", orient == "horizontal", orient)
        panes = list(app.right_paned.panes())
        check("右侧两栏（预览 + 编辑）", len(panes) == 2, len(panes))

        # ---- 图标 / 沙滩条 ----
        import icons
        check("图标模块可用", icons.image("scan", 18) is not None)
        check("沙滩条已挂上", app._beach is not None
              and managed(app._beach))
        check("顶栏下有一道浪",
              getattr(app, "_wave", None) is not None
              and managed(app._wave))

        # ---- 菜单 / 日志 ----
        app._popup_menu = lambda menu, widget: None
        try:
            app._show_more_menu()
            check("「更多」菜单可构建", True)
        except Exception:
            check("「更多」菜单可构建", False,
                  traceback.format_exc().splitlines()[-1])
        check("日志默认收起", app._log_open is False
              and not managed(app.log_body))
        app.toggle_log()
        root.update_idletasks()
        check("日志可展开", app._log_open is True and managed(app.log_body))
        app.toggle_log()
        root.update_idletasks()
        check("日志可收起", app._log_open is False and not managed(app.log_body))

        # ---- 高级折叠 ----
        check("答案编辑控件齐全（输出目录/文件名）",
              app.var_outdir is not None and app.var_outname is not None
              and len(app.var_rect) == 4)

        # ---- 状态 ----
        app._update_status()
        check("状态栏有文案", bool(app.status_label.cget("text")))
    except Exception:
        check("主流程未抛异常", False, traceback.format_exc().splitlines()[-1])
    finally:
        try:
            if app is not None:
                app._beach.stop() if app._beach is not None else None
        except Exception:
            pass
        try:
            root.destroy()
        except Exception:
            pass

    total = len(RESULTS)
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d 项：PASS %d，FAIL %d" % (total, total - len(bad), len(bad)))
    for name, _ok, detail in bad:
        print("  FAIL", name, detail)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
