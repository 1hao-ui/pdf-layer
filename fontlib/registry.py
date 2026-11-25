# -*- coding: utf-8 -*-
"""registry.py — 字库注册表（``字体输出/字库索引.json``）。

为什么要有注册表
----------------
以前字库是「按目录名硬编码」的：``symbols.py`` 只认 ``ZTi-Han`` / ``ZTi-Sym``，
再加一条写死的老字体回退链。结果：

* 用户自己新建的字库无处安放，只能手工改代码；
* 哪些字体是自己的、哪些是第三方测试字体，代码里分不清；
* 缺字回退顺序散落在三处（symbols / hwrender / fontcal）。

现在所有字库都在 ``字体输出/`` 下登记，一个 JSON 说清：id、名字、类型、
遍数、启用状态、校准值。渲染侧只问一句「当前启用的是哪个」，
新增/停用/回退都不用改代码。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
# fontlib/ 在 源码/pdf-layer 下，项目根是上上级
PROJECT_ROOT = os.path.normpath(os.path.join(HERE, "..", "..", ".."))

REGISTRY_NAME = "字库索引.json"

KIND_HAN = "han"
KIND_SYM = "sym"
KIND_MIXED = "mixed"

# 内置字库：id / 目录 / 名称 / 类型 / 说明。
# 只登记**作者本人笔迹**，不再内置任何第三方字体（2026-11-25 用户要求）。
BUILTIN = (
    {
        "id": "ziti-han",
        "name": "ZTi-Han 亲笔汉字",
        "kind": KIND_HAN,
        "dir": "ZTi-Han",
        "passes": ["pass2.ttf", "pass1.ttf"],   # 顺序 = 主字体优先
        "note": "作者亲笔，500 字 × 2 遍；答题/电路/数学常用字。",
    },
    {
        "id": "ziti-sym",
        "name": "ZTi-Sym 亲笔符号",
        "kind": KIND_SYM,
        "dir": "ZTi-Sym",
        "passes": ["pass2.ttf", "pass3.ttf", "pass4.ttf", "pass1.ttf"],
        "note": "作者亲笔，204 字 × 4 遍；数字/拉丁/希腊/运算符/标点/单位。",
    },
)


def font_root():
    """字库根目录：``字体输出/``。

    打包成 exe 后数据在 ``sys._MEIPASS/字体输出``；源码态在项目根。
    可用环境变量 ``ZTIFONT_ROOT`` 覆盖（测试/多套字库并行时用）。
    """
    env = os.environ.get("ZTIFONT_ROOT", "")
    if env and os.path.isdir(env):
        return os.path.abspath(env)
    import sys
    if getattr(sys, "frozen", False):
        mei = getattr(sys, "_MEIPASS", "")
        if mei:
            return os.path.normpath(os.path.join(mei, "字体输出"))
    return os.path.normpath(os.path.join(PROJECT_ROOT, "字体输出"))


def registry_path(root=None):
    return os.path.join(root or font_root(), REGISTRY_NAME)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


# ---------------------------------------------------------------- 读写
_cache = {"path": None, "data": None}


def load(force=False, root=None):
    """读注册表；文件不存在时返回一份「只有内置字库」的内存表（不落盘）。"""
    path = registry_path(root)
    if not force and _cache["path"] == path and _cache["data"] is not None:
        return _cache["data"]
    data = None
    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception:
        data = None
    if not isinstance(data, dict) or "libraries" not in data:
        data = default_registry(root)
    data = _normalize(data, root)
    _cache["path"], _cache["data"] = path, data
    return data


def _normalize(data, root=None):
    """补齐字段、去掉失效条目（目录/字体不在了就标 missing，不删除记录）。"""
    data.setdefault("version", 1)
    data.setdefault("active", {})
    libs = []
    seen = set()
    for lib in data.get("libraries") or []:
        if not isinstance(lib, dict) or not lib.get("id"):
            continue
        lid = str(lib["id"])
        if lid in seen:
            continue
        seen.add(lid)
        lib.setdefault("name", lid)
        lib.setdefault("kind", KIND_HAN)
        lib.setdefault("dir", lid)
        lib.setdefault("passes", [])
        lib.setdefault("enabled", True)
        lib.setdefault("builtin", False)
        lib.setdefault("license", "author")
        lib.setdefault("created", "")
        lib.setdefault("note", "")
        lib.setdefault("char_count", 0)
        lib.setdefault("calibration", {})
        d = _abs_dir(lib, root)
        files = [os.path.join(d, p) for p in lib["passes"]]
        lib["missing"] = not any(os.path.exists(f) for f in files)
        libs.append(lib)
    data["libraries"] = libs
    # 活动项若指向不存在的字库，自动挑第一个可用的同类字库
    for kind in (KIND_HAN, KIND_SYM):
        cur = (data.get("active") or {}).get(kind)
        if not any(l["id"] == cur and not l["missing"] and l["enabled"]
                   for l in libs if l["kind"] in (kind, KIND_MIXED)):
            cand = next((l["id"] for l in libs
                         if l["kind"] in (kind, KIND_MIXED)
                         and not l["missing"] and l["enabled"]), "")
            if cand:
                data.setdefault("active", {})[kind] = cand
    if not data.get("updated"):
        data["updated"] = _now()
    return data


def _abs_dir(lib, root=None):
    d = lib.get("dir") or lib.get("id") or ""
    if os.path.isabs(d):
        return d
    return os.path.normpath(os.path.join(root or font_root(), d))


def save(data, root=None):
    """写回注册表（原子替换，避免写一半断电把索引写坏）。"""
    path = registry_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    data = dict(data or {})
    data["version"] = data.get("version", 1)
    data["updated"] = _now()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    _cache["path"], _cache["data"] = path, data
    return data


def default_registry(root=None):
    """内置字库表（不落盘；``ensure`` 时才写文件）。"""
    root = root or font_root()
    libs = []
    for item in BUILTIN:
        lib = dict(item)
        lib["builtin"] = True
        lib["enabled"] = True
        lib["license"] = "author"
        lib["created"] = ""
        lib["char_count"] = _char_count(_abs_dir(lib, root), lib["passes"])
        libs.append(lib)
    return {
        "version": 1,
        "updated": _now(),
        "active": {KIND_HAN: "ziti-han", KIND_SYM: "ziti-sym"},
        "libraries": libs,
    }


def ensure(root=None):
    """确保注册表存在并已收录磁盘上所有字库目录，返回数据。"""
    root = root or font_root()
    if not os.path.exists(registry_path(root)):
        data = default_registry(root)
    else:
        data = load(force=True, root=root)
    changed = migrate(data, root=root)
    if changed or not os.path.exists(registry_path(root)):
        save(data, root=root)
    return data


def _char_count(d, passes):
    """读主字体的 cmap 统计字数（读不到返回 0）。"""
    for p in passes:
        f = os.path.join(d, p)
        if os.path.exists(f):
            try:
                from fontTools.ttLib import TTFont
                return len(TTFont(f).getBestCmap() or {})
            except Exception:
                return 0
    return 0


# ---------------------------------------------------------------- 查询
def libraries(kind=None, enabled_only=False, root=None):
    data = load(root=root)
    out = []
    for lib in data["libraries"]:
        if kind and lib["kind"] not in (kind, KIND_MIXED):
            continue
        if enabled_only and (not lib["enabled"] or lib.get("missing")):
            continue
        out.append(lib)
    return out


def get(lib_id, root=None):
    for lib in load(root=root)["libraries"]:
        if lib["id"] == lib_id:
            return lib
    return None


def dir_of(lib, root=None):
    if isinstance(lib, str):
        lib = get(lib, root=root)
    return _abs_dir(lib, root) if lib else ""


def pass_paths(lib, root=None, only_existing=True):
    if isinstance(lib, str):
        lib = get(lib, root=root)
    if not lib:
        return []
    d = _abs_dir(lib, root)
    out = [os.path.join(d, p) for p in lib.get("passes") or []]
    return [p for p in out if os.path.exists(p)] if only_existing else out


def main_font(lib, root=None):
    """主字体：passes 里第一个存在的（注册时已按「主→副」排好序）。"""
    paths = pass_paths(lib, root=root)
    return paths[0] if paths else None


def active(kind=KIND_HAN, root=None):
    """当前启用的字库 dict；没有可用的返回 None。"""
    data = load(root=root)
    lid = (data.get("active") or {}).get(kind)
    lib = get(lid, root=root) if lid else None
    if lib and lib["enabled"] and not lib.get("missing"):
        return lib
    # 兜底：同类第一个可用
    for cand in data["libraries"]:
        if cand["kind"] in (kind, KIND_MIXED) and cand["enabled"] \
                and not cand.get("missing"):
            return cand
    return None


def active_dir(kind=KIND_HAN, root=None):
    lib = active(kind, root=root)
    return dir_of(lib, root=root) if lib else ""


def active_font(kind=KIND_HAN, root=None):
    lib = active(kind, root=root)
    return main_font(lib, root=root) if lib else None


def fallback_fonts(kind=KIND_HAN, exclude=None, root=None):
    """缺字回退链：除主字体外、已启用的同类字库（每库只取主字体）。

    只取主字体是有意的：同库的 pass1/pass2 字形覆盖相同，拿变体当回退
    只会让缺字换一种笔迹，还会带进另一套校准值。
    """
    out = []
    excl = os.path.abspath(exclude) if exclude else None
    for lib in libraries(kind=kind, enabled_only=True, root=root):
        p = main_font(lib, root=root)
        if not p:
            continue
        if excl and os.path.abspath(p) == excl:
            continue
        if os.path.abspath(p) not in [os.path.abspath(x) for x in out]:
            out.append(p)
    return out


# ---------------------------------------------------------------- 变更
def register(meta, root=None, replace=True):
    """登记/更新一个字库。

    meta 必填：id, dir, kind；passes 不给时自动扫目录里的 pass*.ttf。
    返回登记后的条目。
    """
    root = root or font_root()
    data = load(root=root)
    if not meta.get("id"):
        raise ValueError("字库必须有 id")
    lib = dict(meta)
    lib.setdefault("name", lib["id"])
    lib.setdefault("kind", KIND_HAN)
    lib.setdefault("enabled", True)
    lib.setdefault("builtin", False)
    lib.setdefault("license", "author")
    lib.setdefault("note", "")
    d = _abs_dir(lib, root)
    if not lib.get("passes"):
        lib["passes"] = discover_passes(d)
    if not lib["passes"]:
        raise ValueError("目录里没有 pass*.ttf：%s" % d)
    lib["char_count"] = _char_count(d, lib["passes"])
    lib["missing"] = False
    lib["created"] = lib.get("created") or _now()
    libs = [l for l in data["libraries"] if l["id"] != lib["id"]]
    if not replace and len(libs) != len(data["libraries"]):
        raise ValueError("字库 id 已存在：%s" % lib["id"])
    libs.append(lib)
    libs.sort(key=lambda l: (not l.get("builtin"), l.get("created", ""), l["id"]))
    data["libraries"] = libs
    save(data, root=root)
    return lib


def discover_passes(d):
    """目录里的 pass*.ttf，按 pass 数字排序（pass2 最清楚，排第一）。"""
    if not os.path.isdir(d):
        return []
    names = [n for n in os.listdir(d)
             if n.lower().startswith("pass") and n.lower().endswith(".ttf")]
    def num(n):
        try:
            return int("".join(c for c in n if c.isdigit()) or 0)
        except ValueError:
            return 0
    names.sort(key=lambda n: (num(n) != 2, num(n)))   # pass2 优先
    return names


def set_active(kind, lib_id, root=None):
    root = root or font_root()
    data = load(root=root)
    lib = get(lib_id, root=root)
    if not lib:
        raise ValueError("没有这个字库：%s" % lib_id)
    if lib.get("missing"):
        raise ValueError("字库文件缺失，不能启用：%s" % lib_id)
    data.setdefault("active", {})[kind] = lib_id
    save(data, root=root)
    return data


def set_enabled(lib_id, enabled, root=None):
    root = root or font_root()
    data = load(root=root)
    lib = get(lib_id, root=root)
    if not lib:
        raise ValueError("没有这个字库：%s" % lib_id)
    lib["enabled"] = bool(enabled)
    save(data, root=root)
    return lib


def unregister(lib_id, delete_files=False, root=None):
    """从索引里移除；``delete_files`` 为真时连目录一起删（有二次确认）。"""
    root = root or font_root()
    data = load(root=root)
    lib = get(lib_id, root=root)
    if not lib:
        return False
    if lib.get("builtin") and delete_files:
        raise ValueError("内置字库不能删除文件")
    d = _abs_dir(lib, root)
    data["libraries"] = [l for l in data["libraries"] if l["id"] != lib_id]
    for kind, cur in list((data.get("active") or {}).items()):
        if cur == lib_id:
            data["active"].pop(kind, None)
    save(data, root=root)
    if delete_files:
        # 只允许删 字体输出/ 下的目录，且路径必须解析后仍在根内
        root_abs = os.path.abspath(root)
        d_abs = os.path.abspath(d)
        if d_abs.startswith(root_abs + os.sep) and os.path.isdir(d_abs):
            shutil.rmtree(d_abs, ignore_errors=True)
    return True


def kind_of_dir(d):
    """按 cmap 判断目录里的字库是汉字还是符号（给迁移用）。"""
    passes = discover_passes(d)
    if not passes:
        return KIND_HAN
    try:
        from fontTools.ttLib import TTFont
        cm = TTFont(os.path.join(d, passes[0])).getBestCmap() or {}
    except Exception:
        return KIND_HAN
    cjk = sum(1 for cp in cm if 0x4E00 <= cp <= 0x9FFF)
    return KIND_HAN if cjk >= max(8, len(cm) * 0.3) else KIND_SYM


def migrate(data, root=None):
    """把磁盘上「有 pass*.ttf 但没登记」的目录补进索引。返回是否有变化。"""
    root = root or font_root()
    if not os.path.isdir(root):
        return False
    known = {os.path.abspath(_abs_dir(l, root)): l for l in data["libraries"]}
    changed = False
    for name in sorted(os.listdir(root)):
        d = os.path.join(root, name)
        if not os.path.isdir(d) or name.startswith("_") or name.startswith("."):
            continue
        if os.path.abspath(d) in known:
            continue
        passes = discover_passes(d)
        if not passes:
            continue
        kind = kind_of_dir(d)
        lid = "lib-" + name
        data["libraries"].append({
            "id": lid, "name": name, "kind": kind, "dir": name,
            "passes": passes, "enabled": True, "builtin": False,
            "license": "unknown", "created": "", "note": "自动发现",
            "char_count": _char_count(d, passes), "missing": False,
            "calibration": {},
        })
        changed = True
    return changed


if __name__ == "__main__":
    import sys
    root = sys.argv[1] if len(sys.argv) > 1 else None
    data = ensure(root)
    print("注册表：", registry_path(root))
    print("活动：", data["active"])
    for lib in data["libraries"]:
        print("  %-18s %-6s %4d 字  %s%s" % (
            lib["id"], lib["kind"], lib["char_count"],
            "启用" if lib["enabled"] else "停用",
            "（缺文件）" if lib.get("missing") else ""))
