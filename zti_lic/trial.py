# -*- coding: utf-8 -*-
"""试用期计时。

策略（02-决策清单 第 4 条）：首次运行起 7 天全功能，到期后锁功能但保留用户数据。

为什么要记两处
--------------
单存一个文件，用户删掉就等于重新试用。所以首次运行时间同时写：
  1. %LOCALAPPDATA%\\ZTi\\trial.json
  2. 注册表 HKCU\\Software\\ZTi\\trial

读取时**取两处里更早的那个**：删掉任何一处都缩不短已经用掉的时间。
两处都删，就回到"首次运行"——这个上限是纯离线方案无法绕过的，如实承认，
不为此过度设计（同 02-决策清单 第 3 条的判断）。

时钟回拨
--------
把"见过的最大日期"记进 last_seen。发现系统时间比 last_seen 还早，
就按 last_seen 继续算 —— 把时间调回过去不会让试用期倒流。
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import sys

from . import paths
from .store import atomic_write

__all__ = [
    "TRIAL_DAYS",
    "trial_status",
    "trial_days_left",
    "reset_trial",
]

TRIAL_DAYS = 7

_REG_PATH = r"Software\ZTi"
_REG_VALUE = "trial"


# ---------------------------------------------------------------------------
# 两处存储
# ---------------------------------------------------------------------------

def _read_file() -> dict | None:
    """读所有候选位置，合并成一条记录（起始取更早、last_seen 取更晚）。

    候选位置包括用户目录和 exe 同目录 —— 安全软件拦截前者时写到了后者，
    两处都要读，否则会误判成"没有记录"而把试用重置。
    """
    found = []
    for path in paths.trial_read_candidates():
        try:
            if not os.path.isfile(path):
                continue
            # utf-8-sig：万一人用记事本编辑过、带上了 BOM，也要能读出来。
            # 否则会被当成"没有记录"，把试用期悄悄重置 —— 那等于送用户 7 天。
            with open(path, "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            if isinstance(data, dict):
                found.append(data)
        except (OSError, ValueError):
            continue
    return _merge_many(found)


def _read_registry() -> dict | None:
    if sys.platform != "win32":
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            raw, _kind = winreg.QueryValueEx(key, _REG_VALUE)
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_file(data: dict) -> None:
    """写所有能写的候选位置（写不进去的静默跳过）。"""
    text = json.dumps(data, ensure_ascii=False)
    for path in paths.trial_candidates():
        try:
            atomic_write(path, text)
        except Exception:
            continue


def _write_registry(data: dict) -> None:
    if sys.platform != "win32":
        return
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _REG_PATH,
                                0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, _REG_VALUE, 0, winreg.REG_SZ,
                              json.dumps(data, ensure_ascii=False))
    except Exception:
        pass


def _write_both(data: dict) -> None:
    _write_file(data)
    _write_registry(data)


# ---------------------------------------------------------------------------
# 日期工具
# ---------------------------------------------------------------------------

def _parse_day(value) -> _dt.date | None:
    if not isinstance(value, str):
        return None
    try:
        return _dt.date.fromisoformat(value)
    except ValueError:
        return None


def _merge_many(records: list) -> dict | None:
    """合并多条记录：起始取更早的，last_seen 取更晚的。"""
    starts, lasts = [], []
    for data in records:
        if not data:
            continue
        start = _parse_day(data.get("start"))
        if start:
            starts.append(start)
        last = _parse_day(data.get("last_seen"))
        if last:
            lasts.append(last)
    if not starts:
        return None
    return {
        "start": min(starts).isoformat(),
        "last_seen": max(lasts + starts).isoformat(),
    }


def _merge(a: dict | None, b: dict | None) -> dict | None:
    """合并两处记录（保留两参形式，方便测试）。"""
    return _merge_many([a, b])


def trial_status(today: _dt.date | None = None) -> dict:
    """返回试用状态。

    {
      "active": True/False,
      "days_left": 剩下天数,
      "start": "YYYY-MM-DD",
      "clock_rollback": 是否检测到时间回拨,
    }
    """
    now = today or _dt.date.today()

    file_data = _read_file()
    reg_data = _read_registry()
    merged = _merge(file_data, reg_data)

    if merged is None:
        # 首次运行：从今天开始计时
        merged = {"start": now.isoformat(), "last_seen": now.isoformat()}
        _write_both(merged)
        return {
            "active": True,
            "days_left": TRIAL_DAYS,
            "start": now.isoformat(),
            "clock_rollback": False,
        }

    start = _dt.date.fromisoformat(merged["start"])
    last_seen = _dt.date.fromisoformat(merged["last_seen"])

    rollback = now < last_seen
    effective = max(now, last_seen)   # 回拨时间不倒流

    elapsed = (effective - start).days
    days_left = TRIAL_DAYS - elapsed
    if days_left < 0:
        days_left = 0

    # 只有向前推进时才写盘，避免每次启动都写文件
    if effective > last_seen:
        _write_both({"start": merged["start"],
                     "last_seen": effective.isoformat()})
        merged["last_seen"] = effective.isoformat()

    return {
        "active": elapsed < TRIAL_DAYS,
        "days_left": days_left,
        "start": start.isoformat(),
        "clock_rollback": rollback,
    }


def trial_days_left(today: _dt.date | None = None) -> int:
    return int(trial_status(today)["days_left"])


def reset_trial() -> None:
    """清空两处试用记录（仅测试和人工支持使用）。"""
    for path in paths.trial_read_candidates():
        try:
            if os.path.isfile(path):
                os.unlink(path)
        except OSError:
            pass
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH, 0,
                                winreg.KEY_WRITE) as key:
                winreg.DeleteValue(key, _REG_VALUE)
        except Exception:
            pass
