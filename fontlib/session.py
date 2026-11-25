# -*- coding: utf-8 -*-
"""session.py — 一次「自建字库」的完整工程（可关掉再打开接着做）。

工作目录默认 ``源码/pdf-layer/out/fontlab/<id>/``：

    session.json        这次建库的全部参数与进度
    <id>.pdf            书写模板
    layout.json         模板网格/字序
    chars.txt/.csv      字表
    pages/pageNNN.npz   每页切好的字块（掩膜）
    blocks.json         每格的体检数据（空白/贴边/偏大偏小）
    font/               pass1.ttf、pass2.ttf、字库.json、预览_字形.png
    preview/            效果示例 PNG

为什么先把掩膜落盘：切字最慢，参数微调/重新建库不该重扫一遍。
"""
from __future__ import annotations

import datetime
import json
import os
import shutil

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_ROOT = os.path.normpath(os.path.join(HERE, "..", "out", "fontlab"))


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def slug(text):
    keep = [c for c in (text or "") if c.isalnum() or c in "-_"]
    return "".join(keep)[:40] or "lib"


class Session:
    """一次建库工程。所有改动调 save() 才会落盘。"""

    def __init__(self, work_dir, data=None):
        self.dir = os.path.abspath(work_dir)
        self.data = data or {}
        self.data.setdefault("created", _now())
        self.data.setdefault("charset", {"preset": "custom", "chars": []})
        self.data.setdefault("template", {})
        self.data.setdefault("images", [])
        self.data.setdefault("intake", {})
        self.data.setdefault("params", {})
        self.data.setdefault("tweaks", {})
        self.data.setdefault("built", {})
        self.data.setdefault("blocks", {})
        self._cells_cache = None

    # ------------------------------------------------------------ 生命周期
    @classmethod
    def create(cls, work_dir, lib_id, name, chars, passes=2,
               cell_pt=32.0, preset="custom", note=""):
        os.makedirs(work_dir, exist_ok=True)
        s = cls(work_dir)
        s.data.update({
            "id": lib_id, "name": name, "note": note,
            "passes": int(passes), "cell_pt": float(cell_pt),
            "charset": {"preset": preset, "chars": list(chars)},
            "created": _now(), "updated": _now(),
        })
        s.save()
        return s

    @classmethod
    def load(cls, path_or_dir):
        d = path_or_dir
        if os.path.isdir(d):
            d = os.path.join(d, "session.json")
        with open(d, encoding="utf-8") as fp:
            data = json.load(fp)
        s = cls(os.path.dirname(os.path.abspath(d)), data)
        return s

    def save(self):
        os.makedirs(self.dir, exist_ok=True)
        self.data["updated"] = _now()
        with open(os.path.join(self.dir, "session.json"), "w",
                  encoding="utf-8") as fp:
            json.dump(self.data, fp, ensure_ascii=False, indent=2)
        return self

    # ------------------------------------------------------------ 基本属性
    @property
    def id(self):
        return self.data.get("id") or slug(os.path.basename(self.dir))

    @property
    def name(self):
        return self.data.get("name") or self.id

    @property
    def chars(self):
        return list(self.data.get("charset", {}).get("chars") or [])

    @property
    def passes(self):
        return int(self.data.get("passes") or 2)

    def set_chars(self, chars, preset=None):
        from . import charsets
        self.data["charset"] = {"preset": preset or self.data["charset"].get(
            "preset", "custom"), "chars": charsets.normalize(chars)}
        self.save()
        return self

    # ------------------------------------------------------------ 模板
    def make_template(self, passes=None, cell_pt=None, title=None,
                      show_reference=True, progress=None):
        from . import template
        passes = int(passes or self.passes)
        cell_pt = float(cell_pt or self.data.get("cell_pt") or 32.0)
        title = title or self.name
        out = os.path.join(self.dir, "template")
        files = template.save_bundle(
            self.chars, out, passes=passes, cell_pt=cell_pt, title=title,
            stem=self.id, show_reference=show_reference, progress=progress)
        self.data["passes"] = passes
        self.data["cell_pt"] = cell_pt
        self.data["template"] = {
            "dir": out, "pdf": files["pdf"], "layout": files["layout"],
            "chars_txt": files["chars_txt"], "pages_per_pass":
                files["layout_data"]["pages_per_pass"],
            "count": files["layout_data"]["count"],
            "passes": passes, "cell_pt": cell_pt,
            "created": _now(),
        }
        self.save()
        return files

    def layout(self):
        from . import template
        p = (self.data.get("template") or {}).get("layout")
        if not p or not os.path.exists(p):
            raise RuntimeError("还没有模板，先生成模板")
        return template.load_layout(p)

    # ------------------------------------------------------------ 识别
    def add_images(self, paths):
        paths = [os.path.abspath(p) for p in paths or []]
        self.data["images"] = paths
        self.save()
        return paths

    def analyze(self, threshold="auto", auto_align=True, progress=None,
                size=None):
        from . import intake
        layout = self.layout()
        images = self.data.get("images") or []
        if not images:
            raise RuntimeError("还没有上传扫描图")
        result = intake.slice_all(images, layout, threshold=threshold,
                                  auto_align=auto_align,
                                  size=size or intake.CELL_NORM,
                                  progress=progress)
        # 掩膜按页存 npz，体检数据进 JSON
        pages_dir = os.path.join(self.dir, "pages")
        os.makedirs(pages_dir, exist_ok=True)
        n_pages = len(layout.get("pages") or [])
        by_page = {}
        cells = result["cells"]
        total = len(cells)
        idx = 0
        for pi in range(n_pages):
            n = len(layout["pages"][pi].get("chars") or [])
            chunk = cells[idx:idx + n]
            idx += n
            chars = [c[0] for c in chunk]
            masks = np.stack([c[1] for c in chunk]) if chunk else \
                np.zeros((0, 240, 240), dtype=bool)
            np.savez_compressed(os.path.join(pages_dir, "page%03d.npz" % pi),
                                chars=np.array(chars),
                                masks=masks.astype(np.uint8))
            by_page[pi] = [c[2] for c in chunk]
        self.data["intake"] = {
            "time": _now(), "pages": result["pages"],
            "warnings": result["warnings"], "threshold": threshold,
            "auto_align": bool(auto_align), "cells": total,
        }
        self.data["blocks"] = {str(k): v for k, v in by_page.items()}
        self._cells_cache = None
        self.save()
        return self.summary()

    def summary(self):
        """识别结果摘要（界面显示）。"""
        blocks = self.data.get("blocks") or {}
        total = sum(len(v) for v in blocks.values())
        blank = sum(1 for v in blocks.values() for m in v if m.get("blank"))
        edge = sum(1 for v in blocks.values() for m in v if m.get("edge"))
        return {"cells": total, "blank": blank, "edge": edge,
                "warnings": (self.data.get("intake") or {}).get("warnings", [])}

    def cells(self):
        """从 npz 还原 [(字符, 掩膜, 体检)]（有缓存，逐字微调时不必反复读盘）。"""
        if getattr(self, "_cells_cache", None) is not None:
            return self._cells_cache
        pages_dir = os.path.join(self.dir, "pages")
        blocks = self.data.get("blocks") or {}
        out = []
        if not os.path.isdir(pages_dir):
            return out
        for name in sorted(os.listdir(pages_dir)):
            if not name.endswith(".npz"):
                continue
            pi = int("".join(c for c in name if c.isdigit()) or 0)
            z = np.load(os.path.join(pages_dir, name))
            chars = [str(c) for c in z["chars"]]
            masks = z["masks"].astype(bool)
            metas = blocks.get(str(pi)) or []
            for i, ch in enumerate(chars):
                meta = dict(metas[i]) if i < len(metas) else {"char": ch}
                out.append((ch, masks[i], meta))
        self._cells_cache = out
        return out

    # ------------------------------------------------------------ 参数
    def suggest(self):
        cells = self.cells()
        if not cells:
            raise RuntimeError("还没有切好的字块")
        from . import autotune
        sug = autotune.suggest(cells)
        self.data["suggest"] = sug["stats"]
        self.data["params"] = dict(sug["params"])
        if not self.data.get("tweaks"):
            self.data["tweaks"] = autotune.auto_tweaks(cells)
        self.save()
        return sug

    def set_params(self, params=None, tweaks=None):
        if params:
            self.data.setdefault("params", {}).update(params)
        if tweaks is not None:
            self.data["tweaks"] = tweaks
        self.save()
        return self.data["params"]

    def tweak(self, ch, scale=None, dx=None, dy=None):
        tw = self.data.setdefault("tweaks", {}).setdefault(
            ch, {"scale": 1.0, "dx": 0.0, "dy": 0.0})
        if scale is not None:
            tw["scale"] = float(scale)
        if dx is not None:
            tw["dx"] = float(dx)
        if dy is not None:
            tw["dy"] = float(dy)
        self.save()
        return tw

    # ------------------------------------------------------------ 建库
    def build(self, lib_id=None, name=None, merge_from=None, activate=True,
              write_calibration=True, progress=None, family=None,
              register=True):
        """建库 → （可选）注册 → 校准 →（可选）启用。

        ``register=False`` 只把 TTF 建在工程目录里，用于「先预览效果」；
        正式发布时再 register=True 拷进 ``字体输出/`` 并写校准。
        merge_from: 现有字库 id 或字体路径列表。给了就把那些字库的字形
        一起并进新库（补字场景：老库 500 字 + 新写 100 字 = 一套 600 字）。
        """
        from . import autotune
        from . import builder
        from . import registry
        cells = self.cells()
        if not cells:
            raise RuntimeError("还没有可建库的字块")
        params = dict(self.data.get("params") or {})
        if not params.get("scale"):
            params = self.suggest()["params"]
        tweaks = dict(self.data.get("tweaks") or {})
        lib_id = lib_id or self.id
        name = name or self.name
        out_dir = os.path.join(self.dir, "font")
        if os.path.isdir(out_dir) and os.path.exists(
                os.path.join(out_dir, "pass1.ttf")):
            # 清掉上一次构建的 TTF，避免新旧混在一起
            for n in os.listdir(out_dir):
                if n.startswith("pass") and n.endswith(".ttf"):
                    try:
                        os.remove(os.path.join(out_dir, n))
                    except OSError:
                        pass

        merged_note = ""
        if merge_from:
            sources = self._resolve_merge(merge_from)
            base = builder.import_fonts(sources, params)
            if base:
                # 已有的字优先用新写的；新库没有的字从旧库导入
                have = {ch for ch, _, _ in cells}
                extra = [(ch, m, {"char": ch, "imported": True,
                                  "pass": p + 1})
                         for ch, ms in base.items() if ch not in have
                         for p, m in enumerate(ms)]
                cells = cells + extra
                merged_note = "并入 %d 字（%d 套）" % (len(base), len(sources))

        meta = builder.build_library(
            cells, out_dir, lib_id, name, passes=self.passes,
            params=params, tweaks=tweaks, family=family, progress=progress)
        target = out_dir
        cal_path = ""
        lib = None
        registered = False
        if register:
            # 字体输出/<id>/ 才是正式位置：拷过去再注册
            target = os.path.join(registry.font_root(), lib_id)
            if os.path.abspath(target) != os.path.abspath(out_dir):
                os.makedirs(target, exist_ok=True)
                # 先清掉上一版残留的 passN.ttf，避免遍数变少时旧文件还在
                for n in os.listdir(target):
                    if n.startswith("pass") and n.lower().endswith(".ttf"):
                        try:
                            os.remove(os.path.join(target, n))
                        except OSError:
                            pass
                for n in os.listdir(out_dir):
                    src = os.path.join(out_dir, n)
                    dst = os.path.join(target, n)
                    if os.path.isdir(src):
                        shutil.copytree(src, dst, dirs_exist_ok=True)
                    else:
                        shutil.copy2(src, dst)
            if write_calibration and meta.get("calibration"):
                cal_path = autotune.write_calibration(meta["calibration"])
                meta["calibration_path"] = cal_path
            lib = registry.register({
                "id": lib_id,
                "name": name,
                "kind": "han",
                "dir": lib_id,
                "passes": [os.path.basename(p) for p in meta["passes_abs"]],
                "char_count": meta["char_count"],
                "created": meta.get("created"),
                "source": "self-built",
                "license": "author",
                "note": merged_note or "自建字库",
                "calibration": {os.path.basename(p): v
                                for p, v in meta["calibration"].items()},
            })
            if activate:
                registry.set_active("han", lib_id)
            registered = True
        self.data["built"] = {
            "id": lib_id, "dir": target, "time": _now(),
            "chars": meta["char_count"], "passes": meta["passes_abs"],
            "calibration": {os.path.basename(p): v
                            for p, v in meta["calibration"].items()},
            "merged": merged_note, "activated": bool(activate and registered),
            "registered": registered,
        }
        self.save()
        meta["registry"] = lib
        meta["target_dir"] = target
        meta["registered"] = registered
        return meta

    def _resolve_merge(self, merge_from):
        from . import registry
        out = []
        items = merge_from if isinstance(merge_from, (list, tuple)) \
            else [merge_from]
        for item in items:
            if not item:
                continue
            if os.path.exists(str(item)) and str(item).lower().endswith(
                    (".ttf", ".otf")):
                out.append(str(item))
                continue
            lib = registry.get(str(item))
            if lib:
                out.extend(registry.pass_paths(lib))
        return out

    # ------------------------------------------------------------ 预览
    def preview(self, text=None, out_png=None, size_pt=12.4, width_pt=404,
                compare_current=True, cal=None):
        """用刚建好的字库渲染一段样文；和当前字库并排。"""
        from . import preview as pv
        built = self.data.get("built") or {}
        paths = built.get("passes") or []
        paths = [p for p in paths if os.path.exists(p)]
        # 建库后 TTF 拷到了 字体输出/<id>/，以那边为准
        if built.get("dir"):
            from . import registry
            target = os.path.join(built["dir"]) if os.path.isabs(
                built["dir"]) else os.path.join(registry.font_root(),
                                                built["dir"])
            if os.path.isdir(target):
                found = [os.path.join(target, n) for n in
                         registry.discover_passes(target)]
                if found:
                    paths = found
        if not paths:
            raise RuntimeError("还没有建库，先点「生成字库」")
        out_png = out_png or os.path.join(self.dir, "preview",
                                          "效果示例.png")
        os.makedirs(os.path.dirname(out_png), exist_ok=True)
        cal = cal or {p: self.data["built"]["calibration"].get(
            os.path.basename(p), {}) for p in paths}
        if compare_current:
            return pv.compare(paths, pv.current_fonts(), text or pv.SAMPLE,
                              out_png, size_pt=size_pt, width_pt=width_pt,
                              cal=cal)
        img, w, h = pv.render_block(paths, text or pv.SAMPLE,
                                    size_pt=size_pt, width_pt=width_pt,
                                    cal=cal)
        img.convert("RGB").save(out_png)
        return out_png

    def preview_glyphs(self, chars=None, out_png=None, size_pt=36):
        from . import preview as pv
        built = self.data.get("built") or {}
        paths = built.get("passes") or []
        if not paths:
            raise RuntimeError("还没有建库")
        current = pv.current_fonts()
        out_png = out_png or os.path.join(self.dir, "preview", "单字对照.png")
        return pv.glyph_compare(paths[0], current[0] if current else paths[0],
                                chars or self.chars[:80], out_png,
                                size_pt=size_pt)

    @classmethod
    def open_existing(cls, lib_id=None):
        """打开最近一次工程（不给 id 就取最新的）。"""
        root = DEFAULT_ROOT
        if not os.path.isdir(root):
            return None
        best = None
        for name in os.listdir(root):
            p = os.path.join(root, name, "session.json")
            if not os.path.exists(p):
                continue
            if lib_id and name != lib_id:
                continue
            mtime = os.path.getmtime(p)
            if best is None or mtime > best[0]:
                best = (mtime, os.path.dirname(p))
        if not best:
            return None
        return cls.load(best[1])
