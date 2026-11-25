# -*- coding: utf-8 -*-
"""机器指纹采集与机器码编解码。

为什么不用 WMI / wmic
---------------------
实测（2026-11-23，Windows 11 + Python 3.14）：

    winreg 读 MachineGuid            0.0 ms   可用
    GetVolumeInformationW 读卷序列号 0.5 ms   可用
    wmic csproduct                    —      Win11 已移除，报 FileNotFoundError
    PowerShell CIM 查主板 UUID        330 ms  可能"拒绝访问"

两点结论：
  1. wmic 在 Win11 上已经不存在，任何依赖它的方案都是定时炸弹；
  2. 每次 CIM 查询 330ms，两次就击穿"启动 < 100ms"红线。

所以这里全部走 **ctypes 直接调 Win32 API + 读注册表**，总耗时 < 5ms，不启子进程。

另外要纠正一个常见误解：Win32_Processor.ProcessorId 是 CPUID 的特性位掩码，
**不是唯一序列号** —— 同型号 CPU 完全相同，拿它当指纹接近零区分度。因此不采用。

取样优先级（越靠前越稳定）
--------------------------
  1. SMBIOS Type-1 UUID      重装系统、格盘都不变（BIOS 升级可能变）
  2. SMBIOS Type-2 主板序列号 同上
  3. 注册表 MachineGuid       重装系统会变
  4. 系统盘卷序列号           格盘会变
  9. 兜底随机 ID             以上全拿不到时生成并持久化，保证至少稳定

版本位会写进机器码首字符，出问题时可以直接看出"这人当初取的是哪一项"。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import sys
import threading

from . import _b32, paths

__all__ = [
    "machine_code",
    "fingerprint",
    "describe_sources",
    "decode_machine_code",
    "normalize_code",
    "canonical_code",
    "MachineCodeError",
]

MACHINE_PREFIX = "ZTI"
_PAYLOAD_BYTES = 9          # 指纹摘要字节数
_CHECKSUM_BYTES = 1         # 校验字节
_DATA_BYTES = _PAYLOAD_BYTES + _CHECKSUM_BYTES   # 共 10 字节 → 16 个 base32 字符
_B32_LEN = 16
CODE_LEN = len(MACHINE_PREFIX) + 1 + _B32_LEN     # ZTI + 版本位 + 16 = 20

# 机器码里允许出现的字符（Crockford 字母表）
_CODE_CHARS = frozenset("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")

# 取样来源 → 机器码版本位
SOURCE_VERSION = {
    "uuid": "1",
    "board": "2",
    "guid": "3",
    "volume": "4",
    "fallback": "9",
}

_cache_lock = threading.Lock()
_cache: dict | None = None


class MachineCodeError(ValueError):
    """机器码格式不合法。"""


# ---------------------------------------------------------------------------
# 各来源的采集器：取不到一律返回 None，绝不抛异常
# ---------------------------------------------------------------------------

def _smbios_table() -> bytes | None:
    """读原始 SMBIOS 表（RSMB）。取不到返回 None。"""
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetSystemFirmwareTable.argtypes = [
            wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD]
        k32.GetSystemFirmwareTable.restype = wt.DWORD
        signature = int.from_bytes(b"RSMB", "little")
        size = k32.GetSystemFirmwareTable(signature, 0, None, 0)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        got = k32.GetSystemFirmwareTable(signature, 0, buf, size)
        if not got:
            return None
        return buf.raw[:got]
    except Exception:
        return None


def _walk_smbios(raw: bytes):
    """逐条产出 (type, body_bytes, strings)。raw 是 RSMB 的完整返回缓冲。"""
    if not raw or len(raw) < 8:
        return
    length = int.from_bytes(raw[4:8], "little")
    table = raw[8:8 + length] if length else raw[8:]
    total = len(table)
    i = 0
    while i + 4 <= total:
        stype = table[i]
        slen = table[i + 1]
        if slen < 4 or i + slen > total:
            break
        body = table[i:i + slen]
        # 结构体后是若干以 NUL 结尾的字符串，以双 NUL 结束
        j = i + slen
        strings: list[str] = []
        while j < total and table[j] != 0:
            end = table.find(b"\x00", j)
            if end < 0:
                j = total
                break
            strings.append(table[j:end].decode("utf-8", "replace"))
            j = end + 1
        yield stype, body, strings
        # 跳过结束的双 NUL
        i = j + 1
        while i < total and table[i] == 0:
            i += 1


def _smbios_uuid() -> str | None:
    """SMBIOS Type-1 的 UUID。全 00 / 全 FF 视为未填写。"""
    raw = _smbios_table()
    if not raw:
        return None
    for stype, body, _strings in _walk_smbios(raw):
        if stype != 1 or len(body) < 24:
            continue
        raw_uuid = body[8:24]
        if raw_uuid == b"\x00" * 16 or raw_uuid == b"\xff" * 16:
            return None
        # SMBIOS 规定前三段是小端，后两段为原序
        b = bytearray(raw_uuid)
        b[0:4] = raw_uuid[0:4][::-1]
        b[4:6] = raw_uuid[4:6][::-1]
        b[6:8] = raw_uuid[6:8][::-1]
        hexed = b.hex().upper()
        return "-".join([hexed[0:8], hexed[8:12], hexed[12:16],
                         hexed[16:20], hexed[20:32]])
    return None


def _smbios_board_serial() -> str | None:
    """SMBIOS Type-2 的主板序列号。"""
    raw = _smbios_table()
    if not raw:
        return None
    for stype, body, strings in _walk_smbios(raw):
        if stype != 2 or len(body) < 8:
            continue
        idx = body[7]           # 序列号字符串索引（从 1 开始）
        if 1 <= idx <= len(strings):
            serial = strings[idx - 1].strip()
            if serial and serial.lower() not in ("none", "not specified",
                                                 "to be filled by o.e.m.",
                                                 "default string", "0"):
                return serial
    return None


def _machine_guid() -> str | None:
    """注册表里的 MachineGuid（每套 Windows 安装唯一）。"""
    if sys.platform != "win32":
        return None
    try:
        import winreg
        access = winreg.KEY_READ
        # 显式读 64 位视图，避免 32 位进程被重定向到 Wow6432Node
        access |= getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Microsoft\Cryptography",
                            0, access) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
        return str(value).strip().lower() or None
    except Exception:
        return None


def _volume_serial() -> str | None:
    """系统盘卷序列号。"""
    if sys.platform != "win32":
        return None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetVolumeInformationW.argtypes = [
            wt.LPCWSTR, wt.LPWSTR, wt.DWORD,
            ctypes.POINTER(wt.DWORD), ctypes.POINTER(wt.DWORD),
            ctypes.POINTER(wt.DWORD), wt.LPWSTR, wt.DWORD]
        k32.GetVolumeInformationW.restype = wt.BOOL
        drive = (os.environ.get("SystemDrive") or "C:") + "\\"
        serial = wt.DWORD(0)
        ok = k32.GetVolumeInformationW(
            drive, None, 0, ctypes.byref(serial), None, None, None, 0)
        if not ok:
            return None
        value = "%08X" % (serial.value & 0xFFFFFFFF)
        # 0000-0000 说明拿不到真实值
        return None if value == "00000000" else value
    except Exception:
        return None


def _fallback_id() -> str:
    """以上全失败时，生成一个随机 ID 并持久化，保证本机至少稳定。"""
    path = paths.machine_id_path()
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="ascii") as f:
                value = f.read().strip()
            if len(value) >= 16:
                return value
    except OSError:
        pass
    value = os.urandom(16).hex().upper()
    try:
        with open(path, "w", encoding="ascii") as f:
            f.write(value)
    except OSError:
        pass
    return value


def _collect() -> dict:
    """采集所有可用来源，返回 {"uuid":..., "board":..., ...}。"""
    return {
        "uuid": _smbios_uuid(),
        "board": _smbios_board_serial(),
        "guid": _machine_guid(),
        "volume": _volume_serial(),
    }


def fingerprint(refresh: bool = False) -> dict:
    """返回本机指纹信息。

    结构：
        {
          "version": "1",           # 机器码版本位
          "source": "uuid",         # 实际采用哪一项
          "value": "...",           # 该项的原始值
          "sources": {...},         # 所有可用来源（诊断用）
        }
    """
    global _cache
    with _cache_lock:
        if _cache is not None and not refresh:
            return dict(_cache)
        sources = _collect()
        chosen = None
        for key in ("uuid", "board", "guid", "volume"):
            if sources.get(key):
                chosen = key
                break
        if chosen is None:
            sources["fallback"] = _fallback_id()
            chosen = "fallback"
        result = {
            "version": SOURCE_VERSION[chosen],
            "source": chosen,
            "value": sources[chosen],
            "sources": {k: v for k, v in sources.items() if v},
        }
        _cache = result
        return dict(result)


def describe_sources(refresh: bool = False) -> dict:
    """诊断用：本机各指纹来源的取值情况。"""
    return dict(fingerprint(refresh=refresh)["sources"])


# ---------------------------------------------------------------------------
# 机器码
# ---------------------------------------------------------------------------

def _crc8(data: bytes) -> int:
    """CRC-8（poly 0x07），用于机器码的手抄校验。"""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 0x07) & 0xFF if crc & 0x80 else (crc << 1) & 0xFF
    return crc


def _digest(fp: dict) -> bytes:
    """指纹 → 10 字节（9 字节摘要 + 1 字节校验）。"""
    material = ("%s|%s|%s" % (fp["version"], fp["source"], fp["value"])).encode("utf-8")
    digest = hashlib.sha256(material).digest()[:_PAYLOAD_BYTES]
    check = _crc8(bytes([ord(fp["version"])]) + digest)
    return digest + bytes([check])


def machine_code(refresh: bool = False) -> str:
    """本机机器码，形如 ``ZTI1-QW2X-9K3M-8N4P-BT7R``（20 字符 + 分隔符）。"""
    fp = fingerprint(refresh=refresh)
    body = _b32.encode(_digest(fp))
    return _b32.group(MACHINE_PREFIX + fp["version"] + body)


def canonical_code(code: str) -> str:
    """去掉分隔符、统一大写 —— 比较和签名前都要先过这一步。

    用户抄机器码时常带着手工加的 '-' 或空格，也可能给 O/I 这类误读字符；
    统一在这里收敛，避免"明明是对的却提示换机"。
    """
    return "".join(ch for ch in str(code).upper() if ch in _CODE_CHARS)


def normalize_code(code: str) -> str:
    """把用户输入的机器码统一成无分隔、大写的规范形式，并校验长度与前缀。"""
    if not isinstance(code, str):
        raise MachineCodeError("机器码必须是文本")
    text = canonical_code(code)
    if len(text) != CODE_LEN:
        raise MachineCodeError("机器码长度不对（应为 %d 个字符）" % CODE_LEN)
    if not text.startswith(MACHINE_PREFIX):
        raise MachineCodeError("机器码应以 %s 开头" % MACHINE_PREFIX)
    return text


def decode_machine_code(code: str) -> dict:
    """解析并校验机器码，返回 {"version":..., "digest": bytes}。

    校验位不对时抛 MachineCodeError —— 用于提示用户抄错了。
    """
    text = normalize_code(code)
    version = text[len(MACHINE_PREFIX)]
    if version not in SOURCE_VERSION.values():
        raise MachineCodeError("机器码版本位非法：%r" % version)
    body = text[len(MACHINE_PREFIX) + 1:]
    try:
        raw = _b32.decode(body)
    except _b32.Base32Error as exc:
        raise MachineCodeError("机器码无法解码：%s" % exc) from exc
    if len(raw) != _DATA_BYTES:
        raise MachineCodeError("机器码长度不对")
    payload, check = raw[:_PAYLOAD_BYTES], raw[_PAYLOAD_BYTES]
    if _crc8(bytes([ord(version)]) + payload) != check:
        raise MachineCodeError("机器码校验位不对，请检查是否抄错")
    return {"version": version, "digest": payload}


def reset_cache() -> None:
    """供测试使用：清掉进程内指纹缓存。"""
    global _cache
    with _cache_lock:
        _cache = None
