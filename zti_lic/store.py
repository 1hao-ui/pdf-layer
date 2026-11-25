# -*- coding: utf-8 -*-
"""授权文件的落地与读取。

两处位置
--------
    首选  %LOCALAPPDATA%\\ZTi\\license.dat    每用户一份，不随文件夹被拷走
    兼容  <exe同目录>\\license.dat             便携/U盘分发时用户手动放进来

两处都会读，**先读到哪份可解析的就用哪份**；机器码必须对得上（见 license.validate），
这才是防复制的关键 —— 光靠文件位置防不住。

写入一律走"临时文件 + 原子替换"，避免用户强杀进程时留下半截文件，
把本来有效的授权写坏（对应 README 硬性要求 #4：授权失败不能让数据丢）。
"""

from __future__ import annotations

import os

from . import paths
from .errors import LicenseError, Reason
from .license import dump_json, load_json, is_license_doc

__all__ = [
    "load_license",
    "save_license",
    "remove_license",
    "license_exists",
    "atomic_write",
]


def atomic_write(path: str, text: str) -> None:
    """原子写文本文件。

    先在**同目录**建临时文件再 os.replace —— 跨目录 rename 可能跨卷，
    那就不是原子操作了。

    这里刻意用 tempfile.mkstemp 而不是手写 os.open：
    打包成 exe 后，手写的 O_WRONLY|O_CREAT|O_EXCL 在 %LOCALAPPDATA% 下
    会被拒（实测 [Errno 13]），而 mkstemp 走的是同一条路却正常。
    不纠结原因，用验证过的那个。

    tempfile 会连带导入 shutil（约 10ms），所以**延迟到真正写盘时才导入**——
    启动路径上只读不写，不为它付这个开销。
    """
    import tempfile
    from . import _console

    directory = os.path.dirname(os.path.abspath(path)) or "."
    _console.debug_log("atomic_write: start %s" % path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".part", dir=directory)
    _console.debug_log("atomic_write: mkstemp ok %s" % tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        _console.debug_log("atomic_write: ok")
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        _console.debug_log("atomic_write: FAILED")
        raise


def load_license() -> dict:
    """读取授权文件。

    返回解析后的容器字典；找不到抛 NO_LICENSE，找到了但都解析失败抛 MALFORMED。
    """
    last_error: LicenseError | None = None
    for path in paths.license_candidates():
        try:
            if not os.path.isfile(path):
                continue
        except OSError:
            continue
        try:
            # utf-8-sig：用户可能用记事本改过授权文件，带 BOM 也要能读
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except OSError as exc:
            last_error = LicenseError(
                Reason.MALFORMED, "授权文件无法读取：%s" % exc)
            continue
        if not text.strip():
            last_error = LicenseError(Reason.MALFORMED, "授权文件是空的")
            continue
        try:
            doc = load_json(text)
        except LicenseError as exc:
            last_error = exc
            continue
        if not is_license_doc(doc):
            last_error = LicenseError(
                Reason.MALFORMED, "这个文件不是本软件的授权文件")
            continue
        return doc

    if last_error is not None:
        raise last_error
    raise LicenseError(Reason.NO_LICENSE)


def save_license(doc: dict) -> str:
    """把授权容器写到磁盘，返回实际写入的路径。

    依次尝试「用户目录 → exe 同目录」，第一个成功的为准。
    （安全软件拦截 %LOCALAPPDATA% 时靠第二条兜底，见 paths.license_write_candidates）
    """
    if not isinstance(doc, dict):
        raise LicenseError(Reason.MALFORMED, "要保存的授权不是对象")
    text = dump_json(doc)
    errors = []
    for path in paths.license_write_candidates():
        try:
            atomic_write(path, text)
            return path
        except OSError as exc:
            errors.append("%s: %s" % (path, exc))
    raise LicenseError(
        Reason.INTERNAL,
        "授权文件写入失败，可能被安全软件拦截。\n" + "\n".join(errors))


def license_exists() -> bool:
    """是否有任一处的授权文件存在（不判断有效性）。"""
    try:
        return any(os.path.isfile(p) for p in paths.license_candidates())
    except OSError:
        return False


def remove_license() -> list[str]:
    """删除所有授权文件，返回被删掉的路径。用于"解除授权"和测试。"""
    removed = []
    for path in paths.license_candidates():
        try:
            if os.path.isfile(path):
                os.unlink(path)
                removed.append(path)
        except OSError:
            pass
    return removed
