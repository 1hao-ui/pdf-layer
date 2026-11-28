# -*- coding: utf-8 -*-
"""importer.py — 直接导入现成字体文件（TTF / OTF / TTC），不用写模板。

和 ``builder`` 的区别
--------------------
``builder`` 是「纸上的手写 → 扫描 → 切字 → 造新 TTF」。
这里是把**已有的字体文件**登记成一个字库：常见来源是用字嗨/其他 App
导出过的手写 OTF、或从别的机器拷来的自己的字库。

导入时自动做四件事：
1. 体检：读 name 表（字体名）、cmap（多少汉字/多少符号）、unitsPerEm；
2. 归档：拷进 ``字体输出/<id>/passN.ttf``（保持「一个目录一套库」的约定）；
3. 校准：按「汉字墨迹高 ≈ 公式层数字墨迹高」算 size_scale / adv_scale，
   写进 ``fonts/fontcal.json``（不同 upem 的字体也能正确对齐）；
4. 登记：写 ``字库.json`` + 注册表，按字形自动判类型（汉字库/符号库）。
"""
from __future__ import annotations

import json
import os
import shutil

from . import autotune, builder, registry

_EXT = (".ttf", ".otf", ".ttc", ".otc")


# ---------------------------------------------------------------- 体检
def _open(path):
    from fontTools.ttLib import TTFont
    if path.lower().endswith((".ttc", ".otc")):
        return TTFont(path, fontNumber=0)
    return TTFont(path)


def probe(path):
    """读一个字体文件的基本信息；读不了返回 {'ok': False, 'error': ...}。"""
    if not path or not os.path.exists(path):
        return {"ok": False, "error": "文件不存在"}
    if not path.lower().endswith(_EXT):
        return {"ok": False, "error": "只支持 TTF/OTF/TTC"}
    try:
        f = _open(path)
        name = f["name"]

        def nm(i):
            try:
                return (name.getDebugName(i) or "").strip()
            except Exception:
                return ""

        cmap = f.getBestCmap() or {}
        cps = list(cmap)
        han = sum(1 for cp in cps if 0x4E00 <= cp <= 0x9FFF)
        ascii_n = sum(1 for cp in cps if 0x20 <= cp <= 0x7E)
        try:
            upem = int(f["head"].unitsPerEm)
        except Exception:
            upem = 1000
        try:
            glyphs = int(f["maxp"].numGlyphs)
        except Exception:
            glyphs = len(cps)
        f.close()
        kind = "han" if han >= max(30, int(len(cps) * 0.25)) else "sym"
        return {
            "ok": True, "path": path, "name": nm(1) or os.path.splitext(
                os.path.basename(path))[0],
            "style": nm(2), "full": nm(4), "family": nm(1),
            "chars": len(cps), "han": han, "ascii": ascii_n,
            "glyphs": glyphs, "upem": upem, "kind": kind,
            "license": nm(13)[:120],
        }
    except Exception as e:      # noqa: BLE001
        return {"ok": False, "path": path, "error": "%r" % (e,)}


def probe_all(paths):
    return [probe(p) for p in paths or []]


# ---------------------------------------------------------------- 导入
def _unique_id(base, root):
    base = (base or "imported").strip() or "imported"
    cand, n = base, 2
    while os.path.exists(os.path.join(root, cand)):
        cand = "%s-%d" % (base, n)
        n += 1
    return cand


def slug(text):
    keep = []
    for ch in (text or ""):
        if ch.isalnum() or ch in "-_":
            keep.append(ch)
        elif ch in " .·、（）()":
            keep.append("-")
    s = "".join(keep).strip("-")[:40]
    return s or "imported"


_slug = slug


def import_fonts(paths, lib_id=None, name=None, kind=None, activate=True,
                 root=None, write_calibration=True,
                 overwrite=False, progress=None):
    """把字体文件导入成字库；返回 meta（与 builder.build_library 同结构）。

    paths  字体文件路径，按 **主→副** 顺序；多个文件 = 同一个库的多遍。
    lib_id 目录名/注册表 id；不传则用名称生成。
    kind   "han" / "sym" / None（自动判断）
    """
    root = root or registry.font_root()
    paths = [os.path.abspath(p) for p in (paths or []) if p]
    if not paths:
        raise ValueError("没有选择字体文件")
    infos = probe_all(paths)
    good = [i for i in infos if i["ok"]]
    if not good:
        raise ValueError("这些文件都不是可读的字体：%s"
                         % "、".join(os.path.basename(p) for p in paths))
    if len(good) != len(paths):
        raise ValueError("有文件读不了：%s" % "、".join(
            os.path.basename(i.get("path", "")) for i in infos if not i["ok"]))

    kind = kind or good[0]["kind"]
    name = name or good[0]["name"] or os.path.splitext(
        os.path.basename(paths[0]))[0]
    lib_id = lib_id or slug(name)
    target = os.path.join(root, lib_id)
    if os.path.isdir(target) and os.listdir(target) and not overwrite:
        if os.path.exists(os.path.join(target, "字库.json")):
            raise FileExistsError("字库 id 已存在：%s（换一个 id 或允许覆盖）"
                                  % lib_id)
        lib_id = _unique_id(lib_id, root)
        target = os.path.join(root, lib_id)
    os.makedirs(target, exist_ok=True)

    # 拷贝成 passN.ttf（顺序 = 主→副），.ttc 取第一面另存为 ttf
    passes = []
    originals = []
    for i, p in enumerate(paths, 1):
        dst = os.path.join(target, "pass%d.ttf" % i)
        if p.lower().endswith((".ttc", ".otc")):
            f = _open(p)
            f.save(dst)
            f.close()
        else:
            shutil.copy2(p, dst)
        passes.append(os.path.basename(dst))
        originals.append(os.path.basename(p))
        if progress:
            progress(i, len(paths), "复制 %s" % os.path.basename(p))

    # 校准（按项目相对路径写 fontcal.json）
    cal = {}
    try:
        cal = autotune.calibration_for([os.path.join(target, n) for n in passes])
        if write_calibration and cal:
            autotune.write_calibration(cal)
    except Exception as e:      # noqa: BLE001
        cal = {}
        cal_error = repr(e)
    else:
        cal_error = ""

    main_font = os.path.join(target, passes[0])
    chars = []
    try:
        from fontTools.ttLib import TTFont
        chars = sorted(chr(cp) for cp in (TTFont(main_font).getBestCmap() or {}))
    except Exception:
        pass
    created = __import__("datetime").date.today().isoformat()
    meta = {
        "id": lib_id,
        "name": name,
        "kind": kind,
        "dir": lib_id,
        "passes": passes,
        "char_count": len(chars),
        "chars": chars,
        "source": "imported",
        "license": "user",
        "created": created,
        "note": "导入的字体文件：%s" % "、".join(originals),
        "originals": originals,
        "font_info": good,
        "calibration": {os.path.basename(k): v for k, v in cal.items()},
    }
    if cal_error:
        meta["calibration_error"] = cal_error
    with open(os.path.join(target, "字库.json"), "w", encoding="utf-8") as fp:
        json.dump(meta, fp, ensure_ascii=False, indent=2)

    # 字形总览（核对导入后是否正常）
    preview = ""
    try:
        preview = builder.glyph_sheet(main_font, target)
    except Exception:
        preview = ""

    lib = registry.register({
        "id": lib_id, "name": name, "kind": kind,
        "dir": lib_id, "passes": passes,
        "char_count": len(chars), "created": created,
        "source": "imported", "license": "user",
        "note": meta["note"],
        "calibration": meta["calibration"],
    })
    if activate and kind in ("han", "mixed"):
        registry.set_active("han", lib_id)
    elif activate and kind == "sym":
        registry.set_active("sym", lib_id)

    meta["registry"] = lib
    meta["target_dir"] = target
    meta["preview"] = preview
    return meta


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法：py -3 -m fontlib.importer 字体1.ttf [字体2.ttf ...]")
        sys.exit(0)
    m = import_fonts(sys.argv[1:], activate=False, write_calibration=False)
    print(json.dumps({k: m[k] for k in ("id", "name", "kind", "char_count",
                                        "passes", "target_dir")},
                     ensure_ascii=False, indent=2))
