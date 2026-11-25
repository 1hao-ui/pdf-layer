# -*- coding: utf-8 -*-
"""把控制台切到 UTF-8。

为什么需要
----------
宿主的 `ai_api.py` 是在 argparse 之后才调 `_utf8_console()` 的，而我们的授权的
校验必须跑在 argparse **之前**（否则看不到 --self-test 这类开关）。结果就是：
授权失败的提示先打到了还是 GBK 代码页的控制台上，中文全变乱码。

用户在这个界面里要读机器码，乱码等于没法用。所以这里自己先切一次。
对窗口程序（gui / 做题窗口）没有任何副作用：它们没有控制台。
"""

from __future__ import annotations

import os
import sys

_done = False


def _log_paths() -> list[str]:
    """按优先级给出可写的日志路径：数据目录 → exe 同目录 → 临时目录。

    数据目录写不进去时（受限环境/权限异常），退到 exe 同目录；
    再不行退到 TEMP。任何一条成功就够 —— 关键是有现场可查。
    """
    from . import paths
    candidates = []
    try:
        candidates.append(os.path.join(paths.appdata_dir(), "zti_debug.log"))
    except Exception:
        pass
    try:
        candidates.append(os.path.join(paths.exe_dir(), "zti_debug.log"))
    except Exception:
        pass
    temp = os.environ.get("TEMP") or os.environ.get("TMP")
    if temp:
        candidates.append(os.path.join(temp, "zti_debug.log"))
    seen, out = set(), []
    for p in candidates:
        key = os.path.abspath(p).lower()
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def debug_log(message: str) -> None:
    """把调试信息追加到日志（数据目录优先，写不进去就换下一处）。

    始终记录：窗口程序看不到 stdout，用户反馈"激活点了没反应"时，
    这个文件是唯一能拿到的现场。只在激活/授权流程里打少量点，
    正常使用不会持续增长；超过 128KB 自动清空。
    """
    import datetime as _dt
    stamp = "%s [%d] %s\n" % (
        _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), os.getpid(), message)
    errors = []
    for path in _log_paths():
        try:
            try:
                if os.path.getsize(path) > 128 * 1024:
                    os.unlink(path)
            except OSError:
                pass
            directory = os.path.dirname(path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(stamp)
                if errors:
                    f.write("  (前几个路径写失败: %s)\n" % "; ".join(errors))
            return
        except Exception as exc:
            errors.append("%s: %s" % (path, exc))
    # 全部失败：无处可写，只能放弃（不能因此影响主流程）



def ensure_utf8_console() -> None:
    """幂等地把 stdout/stderr 和 Windows 控制台代码页切到 UTF-8。"""
    global _done
    if _done:
        return
    _done = True

    for stream in (sys.stdout, sys.stderr):
        try:
            if stream is not None and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except Exception:
            pass


def eprint(*args, **kwargs) -> None:
    """打印到 stderr；stderr 不可用（窗口程序）时静默丢弃。"""
    if sys.stderr is None:
        return
    try:
        print(*args, file=sys.stderr, **kwargs)
    except Exception:
        pass


def oprint(*args, **kwargs) -> None:
    """打印到 stdout；stdout 不可用（窗口程序）时静默丢弃。"""
    if sys.stdout is None:
        return
    try:
        print(*args, **kwargs)
    except Exception:
        pass
