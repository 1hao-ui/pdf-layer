# -*- coding: utf-8 -*-
"""手写字体加密：打包时封存，授权通过后才解密。

为什么要做
----------
ZTi-Han / ZTi-Sym 是真人手写的字形，是这个产品最核心的东西。
打包成绿色版后，`_internal\\字体输出\\*.ttf` 是明文文件——任何人拷走文件夹
就能直接装进 Word / Photoshop 用，甚至换个名字拿去卖。

做法
----
    · 打包时：把字体加密成一个块 `ziti_fonts.zfp`，包里不再有可用 TTF
    · 运行时：授权校验通过后，把字体解密还原到渲染器本来要找的位置
    · 退出时：删掉还原出来的文件

渲染链路（hwrender / symbols / fontcal）**一行都不用改** —— 它们本来就按
`_MEIPASS/字体输出/ZTi-Han/pass2.ttf` 这样的路径找文件，我们把文件放回去即可，
所以渲染速度完全不受影响（实测解密 3.8ms + 写盘 4.8ms，一次性）。

清理时机
--------
1. 正常退出：atexit 删除还原出来的字体；
2. 下次启动：发现上次没清干净（崩溃/强杀），先清再解密；
3. 退出时若文件被占用（FreeType 可能还持有句柄）删不掉，就留给下次启动清。
"""

from __future__ import annotations

import hashlib
import os
import struct
import sys
import threading
import zlib

from ._fontkey import BLOB_NAME, BLOB_VERSION, FONT_KEY

__all__ = [
    "seal",
    "unseal",
    "ensure_unsealed",
    "cleanup",
    "is_sealed_build",
    "blob_path",
    "fonts_root",
    "read_blob_header",
    "describe",
]

_MAGIC = b"ZTIFONT"
_MARKER = ".unsealed"
_FONT_DIRS = ("ZTi-Han", "ZTi-Sym")
_EXT = (".ttf", ".otf")

_lock = threading.Lock()
_unsealed_files: list[str] = []
_atexit_registered = False


# ---------------------------------------------------------------------------
# 位置
# ---------------------------------------------------------------------------

def fonts_root() -> str:
    """「字体输出」目录：优先 _MEIPASS（打包态），否则源码树。"""
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        return os.path.join(mei, "字体输出")
    here = os.path.dirname(os.path.abspath(__file__))
    app = os.path.dirname(os.path.dirname(here))
    return os.path.normpath(os.path.join(app, os.pardir, "字体输出"))


def blob_path() -> str | None:
    """加密块路径；不存在返回 None。"""
    candidates = [
        os.path.join(fonts_root(), BLOB_NAME),
        os.path.join(fonts_root(), "_sealed", BLOB_NAME),
    ]
    app = getattr(sys, "_MEIPASS", None)
    if app:
        candidates.append(os.path.join(app, BLOB_NAME))
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None


def is_sealed_build() -> bool:
    """当前是否是"字体已加密"的打包版。"""
    return blob_path() is not None


# ---------------------------------------------------------------------------
# 加密块格式
# ---------------------------------------------------------------------------
#
#   magic    8 字节  b"ZTIFONT1"
#   version  uint16
#   count    uint16
#   [ name_len uint16, name utf-8, size uint32, crc32 uint32 ] * count
#   payload  各文件内容按顺序拼接后整体加密
#
# 加密：keystream = SHAKE-256(key || 头字节) 取等长，与明文做大整数异或。
# 头部不加密（里面没有内容），但参与密钥导出，改头会导致解密出垃圾并被 CRC 抓到。

def _keystream(key: bytes, header: bytes, length: int) -> bytes:
    return hashlib.shake_256(key + header).digest(length)


def _xor(data: bytes, stream: bytes) -> bytes:
    """大整数异或 —— 比逐字节循环快两个数量级。"""
    if not data:
        return b""
    n = len(data)
    return (int.from_bytes(data, "little")
            ^ int.from_bytes(stream, "little")).to_bytes(n, "little")


# ---------------------------------------------------------------------------
# 封存（构建期）
# ---------------------------------------------------------------------------

def seal(fonts_dir: str, out_path: str | None = None,
         verbose: bool = False) -> dict:
    """把 fonts_dir 下的字体加密成一块。

    返回 {"path": 路径, "files": 文件数, "bytes": 明文字节数}。
    """
    entries = []
    for sub in _FONT_DIRS:
        directory = os.path.join(fonts_dir, sub)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.lower().endswith(_EXT):
                continue
            path = os.path.join(directory, name)
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as f:
                data = f.read()
            entries.append((sub + "/" + name, data))

    if not entries:
        raise FileNotFoundError("没找到任何字体文件：%s" % fonts_dir)

    header = _MAGIC + b"1" + struct.pack("<HH", BLOB_VERSION, len(entries))
    for name, data in entries:
        raw = name.encode("utf-8")
        header += struct.pack("<H", len(raw)) + raw
        header += struct.pack("<II", len(data), zlib.crc32(data) & 0xFFFFFFFF)

    plain = b"".join(data for _name, data in entries)
    cipher = _xor(plain, _keystream(FONT_KEY, header, len(plain)))

    out_path = out_path or os.path.join(fonts_dir, BLOB_NAME)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(header)
        f.write(cipher)

    info = {"path": out_path, "files": len(entries),
            "bytes": len(plain), "header": len(header)}
    if verbose:
        print("已封存 %d 个字体 / %.2f MB -> %s"
              % (len(entries), len(plain) / 1048576, out_path))
    return info


# ---------------------------------------------------------------------------
# 解密（运行期）
# ---------------------------------------------------------------------------

def read_blob_header(path: str) -> dict:
    """解析加密块头部，返回 {"version", "entries": [(name, size, crc)]}。"""
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:7] != _MAGIC[:7] or head[7:8] != b"1":
            raise ValueError("不是本程序的字体数据块")
        version, count = struct.unpack("<HH", head[8:12])
        entries = []
        for _ in range(count):
            raw_len = f.read(2)
            name_len = struct.unpack("<H", raw_len)[0]
            name = f.read(name_len).decode("utf-8")
            size, crc = struct.unpack("<II", f.read(8))
            entries.append((name, size, crc))
        header_len = f.tell()
    return {"version": version, "entries": entries, "header_len": header_len}


def unseal(blob: str | None = None, target_root: str | None = None) -> list[str]:
    """把加密块解密还原成字体文件，返回写出的路径列表。"""
    blob = blob or blob_path()
    if not blob:
        return []
    target_root = target_root or os.path.dirname(os.path.abspath(blob))

    with open(blob, "rb") as f:
        raw = f.read()
    info = read_blob_header(blob)
    header = raw[:info["header_len"]]
    cipher = raw[info["header_len"]:]
    plain = _xor(cipher, _keystream(FONT_KEY, header, len(cipher)))

    written = []
    offset = 0
    for name, size, crc in info["entries"]:
        data = plain[offset:offset + size]
        offset += size
        if len(data) != size or (zlib.crc32(data) & 0xFFFFFFFF) != crc:
            raise ValueError("字体数据校验失败：%s" % name)
        path = os.path.join(target_root, *name.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        written.append(path)
    return written


def _remove(paths: list[str]) -> int:
    removed = 0
    for path in paths:
        try:
            os.unlink(path)
            removed += 1
        except OSError:
            pass
    return removed


def _cleanup_stale(target_root: str) -> None:
    """清掉上次异常退出留下的明文（先清再解密，避免长期滞留）。"""
    for sub in _FONT_DIRS:
        directory = os.path.join(target_root, sub)
        if not os.path.isdir(directory):
            continue
        for name in os.listdir(directory):
            if name.lower().endswith(_EXT):
                try:
                    os.unlink(os.path.join(directory, name))
                except OSError:
                    pass


def cleanup() -> int:
    """删除本次还原出来的字体文件，返回删掉的个数。"""
    global _unsealed_files
    with _lock:
        paths = list(_unsealed_files)
        _unsealed_files = []
    if not paths:
        return 0
    removed = _remove(paths)
    # 目录空了就一并删掉，避免留下空壳目录
    for path in paths:
        directory = os.path.dirname(path)
        try:
            if os.path.isdir(directory) and not os.listdir(directory):
                os.rmdir(directory)
        except OSError:
            pass
    root = os.path.dirname(os.path.dirname(paths[0])) if paths else None
    if root:
        try:
            marker = os.path.join(root, _MARKER)
            if removed == len(paths):
                os.unlink(marker)
        except OSError:
            pass
    return removed


def ensure_unsealed() -> bool:
    """确保字体文件可用。幂等、线程安全。

    · 非打包态（源码运行）：字体本来就是明文，直接返回 True；
    · 没有加密块：说明这版没加密，也返回 True；
    · 有加密块：解密还原。成功返回 True，失败抛异常（由调用方决定怎么提示）。
    """
    global _atexit_registered, _unsealed_files

    blob = blob_path()
    if blob is None:
        return True                      # 源码态或未加密的包

    if not getattr(sys, "_MEIPASS", None):
        return True                      # 源码态不做解密

    target_root = os.path.dirname(os.path.abspath(blob))

    with _lock:
        if _unsealed_files:
            return True                  # 本次已经还原过

        _cleanup_stale(target_root)
        written = unseal(blob, target_root)
        _unsealed_files = written

        try:
            import datetime as _dt
            with open(os.path.join(target_root, _MARKER), "w",
                      encoding="utf-8") as f:
                f.write("%d %s\n" % (os.getpid(),
                                     _dt.datetime.now().isoformat(timespec="seconds")))
        except OSError:
            pass

        if not _atexit_registered:
            import atexit
            atexit.register(cleanup)
            _atexit_registered = True
        return True


def describe() -> str:
    """给诊断用的文字说明。"""
    blob = blob_path()
    if blob is None:
        return "字体未加密（源码态或未封存的包），直接从磁盘读取 TTF"
    return "字体已加密：%s" % blob
