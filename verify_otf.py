# -*- coding: utf-8 -*-
"""verify_otf.py — 字嗨 FontDrawer 导出格式（OTF）的 Pillow / fontTools 兼容性自检。

用法： python verify_otf.py <字体文件或目录> [测试文本]
检查项：表结构、cmap 覆盖、Pillow 能否按 Unicode 取到字形、是否真的有墨迹。
"""
import os
import sys
import glob
from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

DEFAULT_TEXT = "三相电路中，电压等于电流乘以电阻；功率与电流的平方成正比。Ω√≈∑∫∠≤≥"


def check(path, text):
    r = {"file": os.path.basename(path)}
    try:
        tt = TTFont(path)
        r["tables"] = ",".join(
            t for t in ("glyf", "CFF ", "CFF2", "cmap", "head", "hhea", "hmtx",
                        "name", "OS/2", "post", "loca", "maxp") if t in tt)
        r["outline"] = "glyf(TrueType)" if "glyf" in tt else ("CFF" if "CFF " in tt else "?")
        cmap = tt.getBestCmap() or {}
        r["cmap"] = len(cmap)
        r["glyphs"] = tt["maxp"].numGlyphs
        r["upm"] = tt["head"].unitsPerEm
        r["missing"] = "".join(sorted({c for c in text
                                       if not c.isspace() and ord(c) not in cmap}))
        r["family"] = tt["name"].getDebugName(1)
    except Exception as e:
        r["fontTools"] = "FAIL %r" % (e,)
        return r

    try:
        f = ImageFont.truetype(path, 40)
        img = Image.new("L", (1200, 120), 0)
        ImageDraw.Draw(img).text((5, 5), text, font=f, fill=255)
        bb = img.getbbox()
        r["pil"] = "OK" if bb else "OK-but-empty"
        r["ink_bbox"] = bb
        r["advance"] = round(f.getlength(text), 1)
        r["ascdesc"] = f.getmetrics()
    except Exception as e:
        r["pil"] = "FAIL %r" % (e,)
    return r


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    text = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TEXT
    files = ([target] if os.path.isfile(target) else
             sorted(glob.glob(os.path.join(target, "*.otf")) +
                    glob.glob(os.path.join(target, "*.ttf"))))
    if not files:
        print("no font files found in", target)
        return 1
    print("test text:", text)
    print()
    ok = True
    for p in files:
        r = check(p, text)
        print("[%s] %s" % (r.get("pil", "?"), r["file"]))
        for k in ("outline", "tables", "upm", "glyphs", "cmap", "family",
                  "missing", "ink_bbox", "advance", "ascdesc", "fontTools"):
            if k in r:
                v = r[k]
                if k == "missing" and not v:
                    v = "(无缺字)"
                print("    %-10s %s" % (k + ":", v))
        if r.get("pil", "").startswith("FAIL") or r.get("fontTools", "").startswith("FAIL"):
            ok = False
        print()
    print("RESULT:", "ALL PASS" if ok else "HAS FAILURE")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
