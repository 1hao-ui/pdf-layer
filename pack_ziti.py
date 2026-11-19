# -*- coding: utf-8 -*-
"""pack_ziti.py — 把私人手写笔迹字库打包成可公开发布的「Z体 / ZTi」。

来源（本地、不入库）：
  fonts/user/pass1..4.otf          字嗨亲写 4 遍 → 每字 4 个形态（主字库）
  out/my_ziti/fonts/pass1..4.ttf   14 张真实手稿切字 → 扩充字库

产物（入库）：
  fonts/ZTi/ZTi-01..04.otf         主字库，4 形态
  fonts/ZTi/ZTi-Scan-01..04.ttf    扩充字库
  fonts/ZTi/CHARS.txt              字表清单
  fonts/ZTi/preview.png            8 套 × 4 形态预览

只改 name 表 / CFF topDict 的字体名，不动任何字形轮廓、cmap、hmtx。
"""
from __future__ import annotations

import os
import sys

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
SRC_HAND = os.path.join(HERE, "fonts", "user")
SRC_SCAN = os.path.join(HERE, "out", "my_ziti", "fonts")
OUT = os.path.join(HERE, "fonts", "ZTi")

FAMILY_EN = "ZTi"
FAMILY_ZH = "Z体"
COPYRIGHT = "Copyright (c) 2026 1hao-ui. All rights reserved."
LICENSE = "Proprietary. Personal handwriting; all rights reserved."
URL = "https://github.com/1hao-ui/pdf-layer"
VERSION = "Version 1.0"


def _outlines(font):
    """字形轮廓指纹：CFF CharString 字节码 / glyf 坐标，用来证明改名没动字形。"""
    fp = {}
    if "CFF " in font:
        cs = font["CFF "].cff.topDictIndex[0].CharStrings
        for g in font.getGlyphOrder():
            fp[g] = bytes(cs[g].bytecode) if g in cs else b""
    elif "glyf" in font:
        glyf = font["glyf"]
        for g in font.getGlyphOrder():
            gl = glyf[g]
            try:
                coords, ends, flags = gl.getCoordinates(glyf)
                fp[g] = (gl.numberOfContours,
                         tuple((int(x), int(y)) for x, y in coords),
                         tuple(int(e) for e in ends),
                         tuple(int(f) for f in flags))
            except Exception:
                fp[g] = (gl.numberOfContours,)
    return fp


def _set_name(font, name_id, value, platform_id, enc_id, lang_id):
    font["name"].setName(value, name_id, platform_id, enc_id, lang_id)


def rename(src, dst, *, full_en, full_zh, sub_en, sub_zh, ps, index):
    """重写 name 表 + CFF topDict，保存为新文件；返回校验信息。"""
    font = TTFont(src)
    before = _outlines(font)
    cmap_before = dict(font.getBestCmap() or {})
    hmtx_before = dict(font["hmtx"].metrics)

    n = font["name"]
    # 清掉字嗨/扫描工具留下的旧记录，避免 8 个文件装着同一批 MyHand12 / ZitiReal1
    for rec in list(n.names):
        n.removeNames(nameID=rec.nameID, platformID=rec.platformID,
                      platEncID=rec.platEncID, langID=rec.langID)

    # 标准三套记录：Windows 英文 0x409 / Windows 简体中文 0x804 / Mac 英文。
    # 原字嗨字库用 0x404（繁中），简体环境下字体列表不一定显示中文名，故改 0x804。
    # 不再写 platform 0（Unicode）：那条记录的 langID 语义特殊，容易与 ltag 表冲突。
    def put(name_id, en, zh=None):
        _set_name(font, name_id, en, 3, 1, 0x409)
        _set_name(font, name_id, en, 1, 0, 0)
        if zh:
            _set_name(font, name_id, zh, 3, 1, 0x804)

    # 1 家族 / 2 子族 / 3 唯一 ID / 4 全名 / 5 版本 / 6 PS 名
    # 16 排版家族 / 17 排版子族
    # 8 个文件同属「Z体 / ZTi」一个家族，靠 nameID 2/17 的 style 区分套次，
    # 这样在字体列表里是一套字体的 8 个字形变体，而不是 8 款互不相干的字。
    put(1, FAMILY_EN, FAMILY_ZH)
    put(2, sub_en, sub_zh)
    put(3, "%s;%s;%s" % (FAMILY_EN, ps, index))
    put(4, full_en, full_zh)
    put(5, VERSION)
    put(6, ps)
    put(8, "1hao-ui")
    put(9, "1hao-ui (handwritten by the author)")
    put(11, URL)
    put(13, LICENSE)
    put(14, URL)
    put(16, FAMILY_EN, FAMILY_ZH)
    put(17, sub_en, sub_zh)
    put(0, COPYRIGHT)

    # CFF 内部还有一份 PostScript 名 / 家族名，不同步的话部分软件仍显示 MyHand12
    if "CFF " in font:
        cff = font["CFF "].cff
        cff.fontNames = [ps]
        td = cff.topDictIndex[0]
        td.version = VERSION
        td.FullName = full_en
        td.FamilyName = FAMILY_EN
        td.Weight = sub_en
        td.Copyright = COPYRIGHT
        td.Notice = LICENSE

    font.save(dst)

    chk = TTFont(dst)
    assert _outlines(chk) == before, "outlines changed: %s" % dst
    assert dict(chk.getBestCmap() or {}) == cmap_before, "cmap changed: %s" % dst
    assert dict(chk["hmtx"].metrics) == hmtx_before, "hmtx changed: %s" % dst
    assert chk["name"].getDebugName(1), "family name empty: %s" % dst
    ImageFont.truetype(dst, 40)  # Pillow / FreeType 必须能加载
    return len(before), len(cmap_before)


def cjk_chars(path):
    c = set(TTFont(path).getBestCmap() or {})
    return {chr(cp) for cp in c if 0x3400 <= cp <= 0x9FFF or 0xF900 <= cp <= 0xFAFF}


def preview(paths, out_png, chars, title):
    """每套一行、每字四形态的网格，纯 Pillow，不依赖渲染引擎。"""
    cell, head, lab_w, pad = 62, 52, 96, 14
    cols = 4
    W = pad * 2 + lab_w + cols * cell
    H = pad * 2 + head + len(paths) * (cell + 6)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((pad, pad), title, fill=(24, 24, 28), font=_label_font(15))
    for c in range(cols):
        d.text((pad + lab_w + c * cell + 10, pad + 24), "第%d遍" % (c + 1),
               fill=(100, 100, 106), font=_label_font(12))
    for r, p in enumerate(paths):
        y = pad + head + r * (cell + 6)
        d.text((pad, y + cell // 3), os.path.basename(p), fill=(70, 70, 76), font=_label_font(11))
        f = ImageFont.truetype(p, 44)
        for c, ch in enumerate(chars):
            tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
            ImageDraw.Draw(tile).text((10, 2), ch, font=f, fill=(16, 16, 18, 255))
            img.paste(tile.convert("RGB"), (pad + lab_w + c * cell, y),
                      tile.getchannel("A"))
    img.save(out_png)
    return W, H


def _label_font(px):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


def main():
    os.makedirs(OUT, exist_ok=True)
    hand, scan = [], []
    for i in range(1, 5):
        h = os.path.join(SRC_HAND, "pass%d.otf" % i)
        s = os.path.join(SRC_SCAN, "pass%d.ttf" % i)
        dh = os.path.join(OUT, "ZTi-%02d.otf" % i)
        ds = os.path.join(OUT, "ZTi-Scan-%02d.ttf" % i)
        gh, ch = rename(h, dh,
                        full_en="%s Hand %d" % (FAMILY_EN, i),
                        full_zh="%s 亲写 第%d遍" % (FAMILY_ZH, i),
                        sub_en="Hand %d" % i, sub_zh="亲写 第%d遍" % i,
                        ps="ZTi-Hand-%d" % i, index=i)
        gs, cs = rename(s, ds,
                        full_en="%s Scan %d" % (FAMILY_EN, i),
                        full_zh="%s 手稿 第%d遍" % (FAMILY_ZH, i),
                        sub_en="Scan %d" % i, sub_zh="手稿 第%d遍" % i,
                        ps="ZTi-Scan-%d" % i, index=i + 4)
        hand.append(dh)
        scan.append(ds)
        print("[OK] %-22s glyphs=%-4d cmap=%-4d  %s" %
              (os.path.basename(dh), gh, ch, TTFont(dh)["name"].getDebugName(1)))
        print("[OK] %-22s glyphs=%-4d cmap=%-4d  %s" %
              (os.path.basename(ds), gs, cs, TTFont(ds)["name"].getDebugName(1)))

    # 字表清单：两套并集、每字在几套里有形态
    hc = [cjk_chars(p) for p in hand]
    sc = [cjk_chars(p) for p in scan]
    union = set().union(*hc, *sc)
    hi = set.intersection(*hc)
    si = set.intersection(*sc)
    lines = [
        "# Z体 / ZTi — 字表清单",
        "",
        "主字库  ZTi-01..04.otf     字嗨亲写 4 遍，%d 个汉字每字 4 形态" % len(hi),
        "扩充库  ZTi-Scan-01..04.ttf 真实手稿切字，%d 个汉字" % len(si),
        "并集    %d 个汉字" % len(union),
        "",
        "## 主字库（每字均有 4 个不同形态）",
        "".join(sorted(hi)),
        "",
        "## 扩充库（高频字有 4 形态，其余为单形态复用）",
        "".join(sorted(si)),
        "",
        "## 并集",
        "".join(sorted(union)),
        "",
        "## 逐字形态数（8 套合计可用形态）",
    ]
    for ch in sorted(union):
        lines.append("%s %d" % (ch, sum(ch in s for s in hc) + sum(ch in s for s in sc)))
    with open(os.path.join(OUT, "CHARS.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    common = sorted(hi & si)[:4] or sorted(hi)[:4]
    w, h = preview(hand, os.path.join(OUT, "preview_hand.png"),
                   common, "Z体 · 主字库（字嗨亲写 4 遍）")
    print("preview_hand.png  %d x %d" % (w, h))
    w, h = preview(scan, os.path.join(OUT, "preview_scan.png"),
                   common, "Z体 · 扩充库（真实手稿切字）")
    print("preview_scan.png  %d x %d" % (w, h))
    print("union=%d hand=%d scan=%d -> %s" % (len(union), len(hi), len(si), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
