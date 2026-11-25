# -*- coding: utf-8 -*-
"""运行时路径约定。

跟宿主的既有惯例保持一致（见 01-宿主约束与集成点.md 第 2 节）：
  1. 便携优先：exe 同目录用户看得见、能拷；
  2. 用户目录兜底：%LOCALAPPDATA% 下每用户一份，不随文件夹拷走。

授权状态以用户目录为主（拷走文件夹带不走授权），exe 同目录为辅（U 盘分发）。

测试可用环境变量 ZTI_DATA_DIR 覆盖用户目录，避免污染真实的 %LOCALAPPDATA%。
"""

from __future__ import annotations

import os
import sys

APP_FOLDER = "ZTi"
LICENSE_FILENAME = "license.dat"
TRIAL_FILENAME = "trial.json"
MACHINE_ID_FILENAME = "machine.id"

_ENV_OVERRIDE = "ZTI_DATA_DIR"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境里。"""
    return bool(getattr(sys, "frozen", False))


def exe_dir() -> str:
    """exe 所在目录（源码态退回本包上级的仓库目录）。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.dirname(os.path.dirname(here))


def appdata_dir() -> str:
    """%LOCALAPPDATA%\\ZTi，必要时创建。

    没有 LOCALAPPDATA（非 Windows 或受限环境）时退回用户主目录下的隐藏目录。
    """
    override = os.environ.get(_ENV_OVERRIDE)
    if override:
        base = os.path.abspath(override)
    else:
        local = os.environ.get("LOCALAPPDATA")
        if local:
            base = os.path.join(local, APP_FOLDER)
        else:
            base = os.path.join(os.path.expanduser("~"), "." + APP_FOLDER.lower())
    _ensure_dir(base)
    return base


def _ensure_dir(path: str) -> None:
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        pass


def license_candidates() -> list[str]:
    """按优先级返回所有可能的 license.dat 路径。"""
    paths = [os.path.join(appdata_dir(), LICENSE_FILENAME)]
    portable = os.path.join(exe_dir(), LICENSE_FILENAME)
    if os.path.abspath(portable) not in [os.path.abspath(p) for p in paths]:
        paths.append(portable)
    return paths


def license_write_path() -> str:
    """激活后写授权文件的首选位置：用户目录（不随文件夹被拷走）。"""
    return os.path.join(appdata_dir(), LICENSE_FILENAME)


def _portable_fallback(filename: str) -> list[str]:
    """exe 同目录的兜底路径。

    只在打包态启用：源码态下 exe_dir 是仓库目录，往那里写文件只会污染工作区，
    而且真实用户拿到的都是打包版。
    """
    if not is_frozen():
        return []
    return [os.path.join(exe_dir(), filename)]


def license_write_candidates() -> list[str]:
    """授权文件的**写入**位置，按优先级排列。

    用户目录优先；用户目录不可写时退回 exe 同目录。

    这个兜底不是锦上添花：某些安全软件（实测 McAfee，360/火绒同类行为）
    会把未签名程序写 %LOCALAPPDATA% 判为可疑并静默拒绝（Access Denied），
    却允许它写自己的安装目录。没有兜底的话，这类用户永远激活不了。
    """
    primary = os.path.join(appdata_dir(), LICENSE_FILENAME)
    out = [primary]
    for path in _portable_fallback(LICENSE_FILENAME):
        if os.path.abspath(path) != os.path.abspath(primary):
            out.append(path)
    return out


def trial_path() -> str:
    return os.path.join(appdata_dir(), TRIAL_FILENAME)


def trial_candidates() -> list[str]:
    """试用记录的**写入**位置（同 license：用户目录优先、exe 同目录兜底）。"""
    primary = os.path.join(appdata_dir(), TRIAL_FILENAME)
    out = [primary]
    for path in _portable_fallback(TRIAL_FILENAME):
        if os.path.abspath(path) != os.path.abspath(primary):
            out.append(path)
    return out


def trial_read_candidates() -> list[str]:
    """试用记录的**读取**位置：写入位置 + 用户目录（源码态也要能读到旧的）。"""
    out = list(trial_candidates())
    primary = os.path.join(appdata_dir(), TRIAL_FILENAME)
    if os.path.abspath(primary) not in [os.path.abspath(p) for p in out]:
        out.insert(0, primary)
    return out


def machine_id_path() -> str:
    return os.path.join(appdata_dir(), MACHINE_ID_FILENAME)
