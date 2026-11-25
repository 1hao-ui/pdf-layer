# -*- coding: utf-8 -*-
"""激活界面。

三种界面形态，按可用性自动降级：

    tk       三个 exe 的正常情况（PyInstaller 打包时已带 tkinter）
    console  ai_api 这类 headless 场景，或 tk 初始化失败时
    none     两者都不可用（极端环境）→ 只打印人话后退出

这里的所有函数都保证**不抛异常给调用方**（除非用户主动退出），
因为它们在启动路径上，崩了用户就看不到任何提示。
"""

from __future__ import annotations

import os
import sys

from . import activation, license as lic, machine, paths
from ._pubkey import PUBLIC_KEY
from .errors import LicenseError, Reason

__all__ = ["run_activation", "maybe_notify_trial"]

_NOTICE_FILENAME = "trial_notice.txt"
_NOTICE_DAYS = 3          # 试用只剩这么多天时开始提醒


# ---------------------------------------------------------------------------
# 公共入口
# ---------------------------------------------------------------------------

def run_activation(status, mode: str = "auto", app_version: str | None = None) -> bool:
    """显示激活界面，返回是否激活成功。"""
    mode = _resolve_mode(mode)
    if mode == "tk":
        try:
            return _run_tk(status, app_version)
        except SystemExit:
            raise
        except Exception:
            # tk 起不来就退回控制台，绝不让用户面对堆栈
            return _run_console(status, app_version)
    if mode == "console":
        return _run_console(status, app_version)
    _print_banner(status)
    print("当前环境没有可用的图形界面，请在有桌面的电脑上激活。", file=sys.stderr)
    return False


def maybe_notify_trial(status, mode: str = "auto") -> None:
    """试用快到期时提醒一次（每天最多一次）。"""
    days = getattr(status, "days_left", 0) or 0
    if days <= 0 or days > _NOTICE_DAYS:
        return
    if not _should_notify_today():
        return
    text = ("试用还剩 %d 天。\n到期后将无法继续使用，但已保存的项目和导出文件不受影响。"
            % days)
    mode = _resolve_mode(mode)
    if mode == "tk":
        try:
            _simple_message("试用提醒", text, kind="info")
            _mark_notified()
            return
        except Exception:
            pass
    from . import _console
    _console.ensure_utf8_console()
    _console.eprint(text)
    _mark_notified()

def _resolve_mode(mode: str) -> str:
    if mode in ("tk", "console", "none"):
        return mode
    # auto：有 tkinter 且能建窗口才用 GUI
    try:
        import tkinter  # noqa: F401
        return "tk"
    except Exception:
        return "console"


# ---------------------------------------------------------------------------
# 每日提醒节流
# ---------------------------------------------------------------------------

def _notice_path() -> str:
    return os.path.join(paths.appdata_dir(), _NOTICE_FILENAME)


def _should_notify_today(now=None) -> bool:
    import datetime as _dt
    today = (now or _dt.date.today()).isoformat()
    try:
        with open(_notice_path(), "r", encoding="utf-8") as f:
            return f.read().strip() != today
    except OSError:
        return True


def _mark_notified(now=None) -> None:
    import datetime as _dt
    today = (now or _dt.date.today()).isoformat()
    try:
        with open(_notice_path(), "w", encoding="utf-8") as f:
            f.write(today)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 图形界面
# ---------------------------------------------------------------------------

def _run_tk(status, app_version: str | None) -> bool:
    import tkinter as tk
    from tkinter import filedialog, messagebox
    from . import _console
    _console.debug_log("_run_tk: enter (product=%r)" % status.product)

    root = tk.Tk()
    root.title("激活 · %s" % lic.product_display_name(status.product))
    root.resizable(False, False)

    result = {"ok": False, "busy": False}
    machine_text = status.machine_code or machine.machine_code()

    outer = tk.Frame(root, padx=18, pady=16)
    outer.pack(fill="both", expand=True)

    tk.Label(outer, text="本机机器码", anchor="w").pack(fill="x")
    code_var = tk.StringVar(value=machine_text)
    code_entry = tk.Entry(outer, textvariable=code_var, state="readonly",
                          justify="center", font=("Consolas", 12))
    code_entry.pack(fill="x", pady=(2, 4))

    hint = tk.Label(outer, anchor="w", justify="left", wraplength=430,
                    fg="#555",
                    text=("把机器码和卡密发给作者，或在下面直接输入卡密后点激活。\n"
                          "激活成功后本机可离线使用，不需要每次都联网。"))
    hint.pack(fill="x", pady=(0, 10))

    tk.Label(outer, text="卡密 / 授权码", anchor="w").pack(fill="x")
    card_var = tk.StringVar()
    card_entry = tk.Entry(outer, textvariable=card_var, justify="center",
                          font=("Consolas", 12))
    card_entry.pack(fill="x", pady=(2, 10))
    card_entry.focus_set()

    msg_var = tk.StringVar(value=status.message or "")
    msg = tk.Label(outer, textvariable=msg_var, anchor="w", justify="left",
                   wraplength=430, fg="#a00")
    msg.pack(fill="x", pady=(0, 10))

    buttons = tk.Frame(outer)
    buttons.pack(fill="x")

    def copy_code():
        root.clipboard_clear()
        root.clipboard_append(machine_text)
        msg_var.set("机器码已复制到剪贴板。")

    def do_activate(event=None):
        if result["busy"]:
            _console.debug_log("do_activate: ignored (busy)")
            return
        text = card_var.get().strip()
        _console.debug_log("do_activate: text=%r" % text)
        if not text:
            msg_var.set("请先输入卡密，或点「导入授权文件」。")
            return
        result["busy"] = True
        msg_var.set("正在激活，请稍候…")
        for widget in (card_entry, activate_btn, file_btn, copy_btn):
            widget.configure(state="disabled")

        def worker():
            from . import _console
            _console.debug_log("worker start: product=%r text=%r"
                               % (status.product, text))
            try:
                payload = activation.install_from_text_or_card(
                    text, status.product, version=app_version)
                result["ok"] = True
                result["serial"] = payload.get("serial") or ""
                result["error"] = ""
                _console.debug_log("worker ok: serial=%r" % result["serial"])
            except LicenseError as exc:
                result["error"] = exc.message
                _console.debug_log("worker LicenseError: %s" % exc.message)
            except Exception as exc:
                import traceback as _tb
                result["error"] = "激活时出现意外错误：%s" % exc
                _console.debug_log("worker Exception: %s\n%s"
                                   % (exc, _tb.format_exc()))
            finally:
                result["busy"] = False
                _console.debug_log("worker finished, busy=False")

        import threading
        threading.Thread(target=worker, daemon=True).start()
        _poll()

    def _poll():
        if result["busy"]:
            root.after(120, _poll)
            return
        if result["ok"]:
            serial = result.get("serial") or ""
            messagebox.showinfo("激活成功",
                                "授权已写入本机。" + ("\n授权编号：%s" % serial if serial else ""),
                                parent=root)
            root.destroy()
            return
        msg_var.set(result.get("error") or "激活失败。")
        for widget in (card_entry, activate_btn, file_btn, copy_btn):
            widget.configure(state="normal")
        card_entry.focus_set()

    def do_import_file():
        path = filedialog.askopenfilename(
            parent=root, title="选择授权文件",
            filetypes=[("授权文件", "*.dat *.txt *.json"), ("所有文件", "*.*")])
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as exc:
            msg_var.set("文件读取失败：%s" % exc)
            return
        text = content.strip()
        # 文件里可能是离线授权码，也可能是一份 JSON 授权
        if text.startswith("{"):
            try:
                doc = lic.load_json(text)
                activation._verify_and_store(doc, status.product, machine_text)
                result["ok"] = True
            except LicenseError as exc:
                msg_var.set(exc.message)
                return
            messagebox.showinfo("激活成功", "授权文件已导入。", parent=root)
            root.destroy()
            return
        card_var.set(text)
        do_activate()

    def do_quit():
        root.destroy()

    copy_btn = tk.Button(buttons, text="复制机器码", command=copy_code, width=12)
    copy_btn.pack(side="left")
    file_btn = tk.Button(buttons, text="导入授权文件", command=do_import_file, width=14)
    file_btn.pack(side="left", padx=6)
    activate_btn = tk.Button(buttons, text="激活", command=do_activate, width=10,
                             default="active")
    activate_btn.pack(side="left")
    tk.Button(buttons, text="退出", command=do_quit, width=8).pack(side="right")

    root.bind("<Return>", do_activate)
    root.protocol("WM_DELETE_WINDOW", do_quit)
    root.update_idletasks()
    _center(root)
    root.mainloop()
    return bool(result["ok"])


def _center(window) -> None:
    try:
        window.update_idletasks()
        w, h = window.winfo_width(), window.winfo_height()
        sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
        window.geometry("+%d+%d" % ((sw - w) // 2, (sh - h) // 3))
    except Exception:
        pass


def _simple_message(title: str, text: str, kind: str = "info") -> None:
    import tkinter as tk
    from tkinter import messagebox
    root = tk.Tk()
    root.withdraw()
    try:
        if kind == "error":
            messagebox.showerror(title, text)
        else:
            messagebox.showinfo(title, text)
    finally:
        root.destroy()


# ---------------------------------------------------------------------------
# 控制台界面
# ---------------------------------------------------------------------------

def _print_banner(status) -> None:
    from . import _console
    _console.ensure_utf8_console()
    eprint = _console.eprint
    eprint("")
    eprint("=" * 60)
    eprint("  %s 需要激活" % lic.product_display_name(status.product))
    eprint("=" * 60)
    eprint("  本机机器码：%s" % (status.machine_code or "（读取失败）"))
    if status.reason == Reason.WRONG_MACHINE:
        eprint("  现有授权绑定的是另一台电脑，请把上面的机器码发给作者重新申请。")
    elif status.message:
        eprint("  %s" % status.message)
    eprint("=" * 60)
    eprint("")


def _run_console(status, app_version: str | None) -> bool:
    from . import _console
    _console.ensure_utf8_console()
    eprint = _console.eprint
    oprint = _console.oprint

    _print_banner(status)
    oprint("请输入卡密（直接回车退出）。如果已有离线授权码，可整段粘贴。")
    for attempt in range(3):
        try:
            text = input("卡密/授权码 > ").strip()
        except (EOFError, KeyboardInterrupt):
            eprint("")
            return False
        if not text:
            return False
        # 允许直接拖入一个授权文件路径
        if os.path.isfile(text):
            try:
                with open(text, "r", encoding="utf-8-sig") as f:
                    text = f.read().strip()
            except OSError as exc:
                eprint("文件读取失败：%s" % exc)
                continue
        try:
            payload = activation.install_from_text_or_card(
                text, status.product, version=app_version)
        except LicenseError as exc:
            eprint("✗ %s" % exc.message)
            if attempt < 2:
                eprint("  请再试一次。")
            continue
        except Exception as exc:
            eprint("✗ 激活时出现意外错误：%s" % exc)
            continue
        serial = payload.get("serial") or ""
        oprint("✓ 激活成功。" + ("授权编号：%s" % serial if serial else ""))
        return True
    eprint("激活未完成。请把机器码发给作者获取卡密。")
    return False
