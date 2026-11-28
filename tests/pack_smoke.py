# -*- coding: utf-8 -*-
"""tests/pack_smoke.py —— 打包自检入口（**不随产品发布**）。

用途：用 `packaging/ZTi-smoke.spec`（和正式 spec 同一套 excludes/裁剪规则）
打一个控制台 exe，在「裁剪后的依赖集」里验证三件事：

  1. 被保留的依赖能 import：numpy / Pillow / pymupdf / scipy.ndimage / fontTools；
  2. scipy.ndimage 的三个函数可用（optimize/linalg/sparse… 已被排除）；
  3. 渲染结果与开发环境基线**逐像素一致**（由 pack_smoke.json 比对）。

产物：exe 同目录下 pack_smoke.json。退出码 0 = 全部通过。
"""
import hashlib
import json
import os
import sys
import tempfile
import traceback

TEXT = "三相电路中，相电压等于线电压除以根号三。电阻 R=10Ω，电流 I=220/5=44A。"
FORMULA = (r"视在功率 $S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，"
           r"功率因数 $\cos\varphi\approx 0.999$。")


def _base():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _out_dir():
    """产物目录：默认 exe 同目录；源码模式下可用 PACK_SMOKE_OUT 指定。"""
    return os.environ.get("PACK_SMOKE_OUT") or _base()


def main():
    rep = {"ok": False, "frozen": bool(getattr(sys, "frozen", False)),
           "steps": {}}

    # ---- 1. 依赖导入 + ndimage 可用性 ----
    try:
        import numpy as np
        from PIL import Image
        import pymupdf
        from scipy import ndimage
        import fontTools

        rep["steps"]["imports"] = {
            "numpy": np.__version__, "pillow": Image.__version__,
            "pymupdf": getattr(pymupdf, "__version__", "?"),
            "fontTools": fontTools.version,
        }
        # 被裁掉的子模块不该被拉进来
        leaked = []
        for m in sys.modules:
            if m.startswith(("pandas", "matplotlib", "lxml")):
                leaked.append(m)
            elif m.startswith("scipy.") and not m.startswith(
                    ("scipy.ndimage", "scipy.special", "scipy._lib",
                     "scipy._cyutility", "scipy.version")):
                leaked.append(m)
        rep["steps"]["leaked"] = sorted(leaked)

        a = np.array([[0, 1, 1, 0], [0, 1, 1, 0], [0, 0, 0, 0]], dtype=bool)
        d = ndimage.distance_transform_edt(a)
        m = ndimage.maximum_filter(d, size=3)
        lab, n = ndimage.label(a, structure=np.ones((3, 3), dtype=int))
        rep["steps"]["ndimage"] = {
            "edt": [round(float(v), 6) for v in np.ravel(d)],
            "maxf": [round(float(v), 6) for v in np.ravel(m)],
            "labels": int(lab.max()), "n": int(n),
            "sizes": sorted(int(x) for x in np.bincount(np.ravel(lab))[1:]),
        }
    except Exception:
        rep["error"] = traceback.format_exc()
        return _write(rep)

    # ---- 2. 渲染基线 ----
    try:
        root = sys._MEIPASS if getattr(sys, "frozen", False) else _base()
        sys.path.insert(0, root)
        sys.path.insert(0, _base())
        os.chdir(root)
        import symbols
        hand = symbols.make_han_hand()
        rep["steps"]["font"] = {
            "main": os.path.basename(hand.font_path),
            "han_dir": os.path.basename(symbols.han_dir() or ""),
        }
        for tag, text in (("text", TEXT), ("formula", FORMULA)):
            img, w, h = hand.render_block(text, width_pt=404)
            rep["steps"][tag] = {
                "w": round(w, 3), "h": round(h, 3),
                "size": list(img.size), "mode": img.mode,
                "sha": hashlib.sha256(img.tobytes()).hexdigest(),
            }
    except Exception:
        rep["error"] = traceback.format_exc()
        return _write(rep)

    # ---- 3. pymupdf 往返：打开 PDF → 取像素 → 贴图 → 另存 ----
    try:
        root = sys._MEIPASS if getattr(sys, "frozen", False) else _base()
        src = None
        for cand in (os.path.join(root, "pack_fixture.pdf"),
                     os.path.join(root, "作业A.pdf"),
                     os.path.join(_base(), "pack_fixture.pdf"),
                     os.path.join(_base(), "tests", "fixtures", "worksheet",
                                  "作业A.pdf")):
            if os.path.exists(cand):
                src = cand
                break
        if src:
            doc = pymupdf.open(src)
            page = doc[0]
            pix = page.get_pixmap(matrix=pymupdf.Matrix(1.0, 1.0), alpha=False)
            img, _w, _h = hand.render_block("自检", width_pt=120)
            tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            tmp.close()
            img.save(tmp.name)
            page.insert_image(pymupdf.Rect(40, 40, 200, 80), filename=tmp.name,
                              overlay=True, keep_proportion=False)
            out = os.path.join(_out_dir(), "pack_smoke_out.pdf")
            doc.save(out, deflate=True, garbage=3)
            doc.close()
            os.unlink(tmp.name)
            rep["steps"]["pymupdf"] = {
                "page_px": [pix.width, pix.height],
                "out_pdf": os.path.basename(out),
                "out_size": os.path.getsize(out) if os.path.exists(out) else 0,
            }
        else:
            rep["steps"]["pymupdf"] = {"skipped": "没有 pack_fixture.pdf"}
    except Exception:
        rep["error"] = traceback.format_exc()
        return _write(rep)

    rep["ok"] = True
    return _write(rep)


def _write(rep):
    path = os.path.join(_out_dir(), "pack_smoke.json")
    try:
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(rep, fp, ensure_ascii=False, indent=2)
    except Exception:
        pass
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
