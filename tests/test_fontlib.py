# -*- coding: utf-8 -*-
"""tests/test_fontlib.py — 自建字库链路的无头测试。

跑法（在 源码/pdf-layer 下）：
    py -3 tests\\test_fontlib.py            # 全部
    py -3 tests\\test_fontlib.py -k char    # 只跑名字含 char 的用例

不依赖 pytest：每个用例是 test_* 函数，main() 逐个跑并汇总。
"""
import os
import shutil
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)
os.chdir(REPO)

import numpy as np                                             # noqa: E402
from PIL import Image, ImageDraw, ImageFont                    # noqa: E402

from fontlib import (autotune, builder, charsets, intake,      # noqa: E402
                     registry, session, template)

HAN = os.path.join(REPO, "..", "..", "字体输出", "ZTi-Han", "pass2.ttf")
SYM = os.path.join(REPO, "..", "..", "字体输出", "ZTi-Sym", "pass2.ttf")


# ---------------------------------------------------------------- 字符集
def test_charset_counts():
    assert len(charsets.gb2312_level1()) == 3755
    assert len(charsets.gb2312_all()) == 6763
    assert len(charsets.gb2312_level2()) == 3008
    assert len(charsets.cjk_unified()) == 20902
    assert charsets.is_rare("峣") and not charsets.is_rare("的")


def test_charset_normalize_and_missing():
    chars = charsets.normalize("三相 电路三相\n\t")
    assert chars == ["三", "相", "电", "路"]
    covered = charsets.font_chars(HAN)
    assert "三" in covered
    miss = charsets.missing_from("三除根", [HAN])
    assert "三" not in miss and "除" in miss
    d = charsets.describe("三相 R=10Ω，≈52.2")
    # describe() 先去重：1/0/5/2 各一次 → 数字 4
    assert d["汉字"] == 2 and d["数字"] == 4 and d["拉丁"] == 1


# ---------------------------------------------------------------- 模板
def test_template_pagination():
    chars = charsets.gb2312_level1()[:700]
    lay = template.make_layout(chars, passes=2)
    g = lay["grid"]
    assert g["cols"] * g["rows"] == g["per_page"]
    assert lay["pages_per_pass"] == 3          # 330/页
    assert len(lay["pages"]) == 6
    assert lay["pages"][2]["chars"] == chars[660:700]
    assert lay["pages"][3]["start"] == 0       # 第 2 遍重新从第 1 个字开始


def test_template_bundle_roundtrip():
    tmp = tempfile.mkdtemp(prefix="tpl_")
    try:
        chars = list("三相电路电压电流电阻功率因数")
        files = template.save_bundle(chars, tmp, passes=1, write_pdf=False)
        assert os.path.exists(files["layout"])
        assert os.path.exists(files["chars_txt"])
        lay = template.load_layout(files["layout"])
        assert lay["pages"][0]["chars"] == charsets.normalize(chars)
        assert lay["count"] == len(charsets.normalize(chars))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 注册表
def test_registry_lifecycle():
    tmp = tempfile.mkdtemp(prefix="reg_")
    old = os.environ.get("ZTIFONT_ROOT")
    os.environ["ZTIFONT_ROOT"] = tmp
    try:
        d = registry.ensure(tmp)
        assert (d.get("active") or {}).get("han") == "ziti-han"
        libdir = os.path.join(tmp, "test-lib")
        os.makedirs(libdir)
        shutil.copy2(HAN, os.path.join(libdir, "pass2.ttf"))
        shutil.copy2(HAN, os.path.join(libdir, "pass1.ttf"))
        lib = registry.register({"id": "test-lib", "name": "测试库",
                                 "dir": "test-lib", "kind": "han"})
        assert lib["char_count"] > 0
        lib2dir = os.path.join(tmp, "test-lib2")
        os.makedirs(lib2dir)
        shutil.copy2(HAN, os.path.join(lib2dir, "pass2.ttf"))
        registry.register({"id": "test-lib2", "name": "测试库2",
                           "dir": "test-lib2", "kind": "han"})
        registry.set_active("han", "test-lib")
        assert registry.active_font("han").endswith("pass2.ttf")
        fb = registry.fallback_fonts("han",
                                     exclude=registry.active_font("han"))
        assert any("test-lib2" in p for p in fb), fb
        assert registry.unregister("test-lib", delete_files=True)
        assert not os.path.exists(libdir)
        registry.unregister("test-lib2", delete_files=True)
    finally:
        if old is None:
            os.environ.pop("ZTIFONT_ROOT", None)
        else:
            os.environ["ZTIFONT_ROOT"] = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 识别
def test_otsu_and_markers():
    gray = Image.new("L", (400, 400), 250)
    ImageDraw.Draw(gray).rectangle((100, 100, 300, 300), fill=20)
    t = intake.otsu(gray)
    assert 20 < t < 250
    page = Image.new("L", (int(595 * 2.78), int(842 * 2.78)), 255)
    d = ImageDraw.Draw(page)
    for (x, y) in intake._marker_centers(page.width, page.height,
                                         page.width / 595.0):
        d.rectangle((x - 19, y - 19, x + 19, y + 19), fill=0)
    centers = intake.find_markers(page)
    assert centers and len(centers) == 4
    warped = intake.align(page, centers)
    assert abs(warped.size[0] - page.size[0]) <= 2
    assert abs(warped.size[1] / warped.size[0] - 842.0 / 595.0) < 0.01


def test_slice_synthetic_page():
    chars = list("三相电路电压电流")
    lay = template.make_layout(chars, passes=1)
    img = __import__("fontlib.selftest", fromlist=["synth_page"]).synth_page(
        lay, 0, [HAN, SYM], dpi=150)
    tmp = tempfile.mkdtemp(prefix="scan_")
    try:
        p = os.path.join(tmp, "p.jpg")
        img.save(p, quality=90)
        rep = intake.slice_all([p], lay, threshold="auto", auto_align=True)
        assert len(rep["cells"]) == len(chars)
        assert sum(1 for _, _, m in rep["cells"] if not m["blank"]) >= len(chars) - 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 建库
def test_mask_to_glyph_geometry():
    mask = np.zeros((240, 240), dtype=bool)
    mask[60:180, 90:150] = True          # 120 高 × 60 宽
    g, ink = builder.mask_to_glyph(mask, 1000 / 240.0 * 0.7, 100)
    assert ink == 120 * 60
    assert g.numberOfContours > 0


def test_build_library_and_calibration():
    tmp = tempfile.mkdtemp(prefix="build_")
    old = os.environ.get("ZTIFONT_ROOT")
    os.environ["ZTIFONT_ROOT"] = tmp
    try:
        chars = list("三相电路")
        cells = []
        for i, ch in enumerate(chars):
            mask = np.zeros((240, 240), dtype=bool)
            mask[40:200, 40:200] = True
            cells.append((ch, mask, {"char": ch, "h": 0.67, "w": 0.67,
                                     "bottom": 0.83, "cx": 0.5, "ink": 25600,
                                     "pass": 1, "page": 0}))
        params = autotune.suggest(cells)["params"]
        meta = builder.build_library(cells, os.path.join(tmp, "lib"), "t1",
                                     "测试", passes=1, params=params)
        from fontTools.ttLib import TTFont
        cm = set(TTFont(meta["passes_abs"][0]).getBestCmap() or {})
        assert {ord(c) for c in chars} <= cm
        cal = autotune.calibration_for(meta["passes_abs"])
        assert cal and all(0.5 < v["size_scale"] < 2.5 for v in cal.values())
    finally:
        if old is None:
            os.environ.pop("ZTIFONT_ROOT", None)
        else:
            os.environ["ZTIFONT_ROOT"] = old
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 端到端
def test_end_to_end_selftest():
    from fontlib import selftest
    tmp = tempfile.mkdtemp(prefix="e2e_")
    try:
        rep = selftest.run(work_dir=tmp)
        assert rep["ok"], rep
        assert rep["built"] >= 20
        assert not rep["missing"]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------- 清理口径
def test_no_third_party_fonts():
    fonts_dir = os.path.join(REPO, "fonts")
    for name in ("LongCang-Regular-merged.ttf",
                 "MaShanZheng-Regular-merged.ttf",
                 "ZhiMangXing-Regular-merged.ttf"):
        assert not os.path.exists(os.path.join(fonts_dir, name)), name
    for lib in registry.libraries():
        assert lib.get("license") == "author", lib["id"]


# ---------------------------------------------------------------- runner
def main(argv=None):
    only = None
    if argv and "-k" in argv:
        only = argv[argv.index("-k") + 1]
    cases = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in cases:
        if only and only not in name:
            continue
        try:
            fn()
            print("PASS  %s" % name)
        except Exception:                      # noqa: BLE001
            failed += 1
            print("FAIL  %s" % name)
            traceback.print_exc()
    print()
    if failed:
        print("失败 %d 项" % failed)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
