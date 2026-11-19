# -*- coding: utf-8 -*-
"""multifont.py — 多套「真人笔迹」字库实现同字多形的接入工具。

配合字嗨手写字型产生器 https://buttaiwan.github.io/writemyfont/ 使用：
把同一份字表写 N 遍、导出 N 份 OTF，本模块负责字表生成、字体自检、以及
按「该字第几次出现」循环切换字库的渲染（改由 hwrender.Hand 实现）。

子命令
------
  list    从正文提取去重字表，生成字嗨「备份档」，导入后「使用者字表」只含这些字，
          不用写满 jf7000 的 7000 字。默认生成 4 份（每套一份，字体名不同）。
  check   校验 N 份 OTF：Pillow 能否加载、表结构、字表是否一致、正文有无缺字、
          每个字实际能拿到几种形态。用户导出字体后第一步就该跑它。
  render  出对照样张：单套（同字同形） vs N 套循环（同字多形），外加重复字形态网格。

典型流程见 README / 对话中的 SOP；这里只负责可自动化的部分。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys

from PIL import Image, ImageDraw, ImageFont
from fontTools.ttLib import TTFont

import hwrender

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_FONT_DIR = os.path.join(HERE, "fonts", "user")
DEFAULT_TEXT = (
    "三相电路中，电压等于电流乘以电阻；电流通过电阻时产生功率，"
    "功率与电流的平方成正比。三个电路，三种情况，结果都一样。"
)


# ---------------------------------------------------------------- 通用工具
def discover_fonts(d, expect=4):
    """按文件名自然序发现 OTF/TTF（pass1..pass4 之类命名即可）。"""
    files = sorted(glob.glob(os.path.join(d, "*.otf")) +
                   glob.glob(os.path.join(d, "*.ttf")))
    return files


def cmap_of(path):
    return set(TTFont(path).getBestCmap() or {})


def outline_kind(path):
    t = TTFont(path)
    return "CFF" if "CFF " in t else ("glyf" if "glyf" in t else "?")


def text_chars(text):
    """正文需要的去重字符（去空白、剔除 $...$ 公式——公式由 math_font 渲染）。

    保留标点与 ASCII 数字字母：它们同样由用户字库渲染，缺了会回退成别的风格。
    """
    s = re.sub(r"\$[^$]*\$", "", text)
    out, seen = [], set()
    for ch in s:
        if ch.isspace() or ch in seen:
            continue
        seen.add(ch)
        out.append(ch)
    return out


def glyph_name(ch):
    """字嗨 customGlyphs 用的字形名：uniXXXX (BMP) / uXXXXX (非 BMP)。"""
    cp = ord(ch)
    return "uni%04X" % cp if cp <= 0xFFFF else "u%05X" % cp


def _load_text(args):
    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            return f.read()
    return args.text or DEFAULT_TEXT


def _label_font(px):
    for p in ("C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        if os.path.exists(p):
            return ImageFont.truetype(p, px)
    return ImageFont.load_default()


# ---------------------------------------------------------------- list
def cmd_list(args):
    text = _load_text(args)
    chars = text_chars(text)
    if not chars:
        print("text is empty")
        return 1
    names = ",".join(glyph_name(c) for c in chars)
    os.makedirs(args.out, exist_ok=True)

    # 纯字表（人看的）：每行 20 字，方便对照着写
    with open(os.path.join(args.out, "zihi_chars.txt"), "w", encoding="utf-8") as f:
        for i in range(0, len(chars), 20):
            f.write("".join(chars[i:i + 20]) + "\n")

    for i in range(1, args.count + 1):
        fam = "%s%d" % (args.family, i)
        # 字嗨备份档格式：每行 key<TAB>value（见 fontdrawer.js exportDataButton）
        # notNewFlag=Y 让导入重载后跳过「歡迎使用」設定精靈，直接进书写界面。
        lines = [
            "fontNameEng\t%s" % fam,
            "fontNameCJK\t我的手寫字型%d" % i,
            "customGlyphs\t%s" % names,
            "saveAsTester\tY",
            "testSerialNo\t1",
            "notNewFlag\tY",
        ]
        p = os.path.join(args.out, "zihi_pass%d.txt" % i)
        with open(p, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    print("chars: %d  ->  %s/zihi_chars.txt" % (len(chars), args.out))
    print("glyph names: %d bytes" % len(names))
    print("backups: %s/zihi_pass1.txt ... zihi_pass%d.txt" % (args.out, args.count))
    print("import each backup in 字嗨 -> 選「使用者字表」-> 逐字書寫 -> 下載字型OTF檔案")
    return 0


# ---------------------------------------------------------------- check
def cmd_check(args):
    paths = discover_fonts(args.fonts)
    if not paths:
        print("NO FONTS in %s" % args.fonts)
        print("put your 字嗨 OTF files there (pass1.otf ... pass4.otf)")
        return 1
    text = _load_text(args)
    chars = text_chars(text)
    print("font dir : %s" % args.fonts)
    print("fonts    : %d" % len(paths))
    print("text     : %d unique chars" % len(chars))
    print()

    cmaps, ok = [], True
    for p in paths:
        try:
            c = cmap_of(p)
            kind = outline_kind(p)
            f = ImageFont.truetype(p, 40)
            probe = Image.new("L", (300, 80), 0)
            ImageDraw.Draw(probe).text((4, 4), "三电", font=f, fill=255)
            ink = probe.getbbox()
            fam = TTFont(p)["name"].getDebugName(1)
            print("[OK] %-22s %-4s cmap=%-6d family=%s ink=%s" %
                  (os.path.basename(p), kind, len(c), fam, bool(ink)))
            if not ink:
                print("     !! Pillow loaded but glyph is EMPTY")
                ok = False
            cmaps.append(c)
        except Exception as e:
            print("[FAIL] %-22s %r" % (os.path.basename(p), e))
            ok = False
            cmaps.append(set())
    print()

    if len(paths) != args.expect:
        print("WARN: expected %d fonts, got %d" % (args.expect, len(paths)))

    # 字表一致性：4 套必须写同一批字，否则同字多形的形态数会参差
    base = cmaps[0]
    for i, c in enumerate(cmaps):
        diff = len(base - c) + len(c - base)
        print("set %d vs set 1: %d chars differ" % (i + 1, diff))
    print()

    # 每个字实际能拿到几种形态
    dist = {}
    per_char = {}
    for ch in chars:
        k = sum(1 for c in cmaps if ord(ch) in c)
        dist[k] = dist.get(k, 0) + 1
        if k < len(paths):
            per_char[ch] = k
    print("variant coverage (how many distinct forms each char can get):")
    for k in sorted(dist, reverse=True):
        print("  %d forms : %d chars" % (k, dist[k]))
    if per_char:
        shown = "".join(sorted(per_char)[:60])
        print("  incomplete: %s%s" % (shown, " ..." if len(per_char) > 60 else ""))
        print("  (missing chars fall back to the main font, no tofu blocks)")
    print()
    print("RESULT: %s" % ("PASS" if ok else "HAS FAILURE"))
    return 0 if ok else 1


# ---------------------------------------------------------------- render
def cmd_render(args):
    paths = discover_fonts(args.fonts)
    if not paths:
        print("NO FONTS in %s" % args.fonts)
        return 1
    text = _load_text(args)
    seed = args.seed
    width = args.width

    # 同一 seed：字体选择不消耗 rng，两行的逐字扰动完全一致，差异只来自字形本身
    single = hwrender.Hand(paths[0], size_pt=args.size, seed=seed, jitter=args.jitter)
    multi = hwrender.Hand(paths[0], size_pt=args.size, seed=seed, jitter=args.jitter,
                          variant_fonts=paths[1:])
    img1, w1, h1 = single.render_block(text, width_pt=width)
    img2, w2, h2 = multi.render_block(text, width_pt=width)

    pad, lab, gap = 16, 26, 14
    W = max(img1.width, img2.width) + pad * 2
    rows = [("A  单套字库：同一个字永远同一个形态", img1),
            ("B  %d 套循环：同一个字按出现次数换形态" % len(paths), img2)]
    H = pad * 2 + sum(lab + im.height + gap for _, im in rows)
    canvas = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(canvas)
    lf = _label_font(15)
    y = pad
    for title, im in rows:
        d.text((pad, y), title, fill=(30, 30, 34), font=lf)
        y += lab
        canvas.paste(im.convert("RGB"), (pad, y), im.convert("RGBA").getchannel("A"))
        y += im.height + gap
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    canvas.save(args.out)
    print("compare -> %s  (%d x %d)" % (args.out, W, H))
    print("  single %.1f x %.1f pt | multi %.1f x %.1f pt" % (w1, h1, w2, h2))

    # 重复字形态网格：挑正文中出现 >= 2 次的字，逐套渲染同一字
    grid = args.grid
    if grid:
        from collections import Counter
        plain = re.sub(r"\$[^$]*\$", "", text)
        cnt = Counter(c for c in plain if not c.isspace() and not c.isascii())
        picks = [c for c, n in cnt.most_common() if n >= 2][:args.grid_chars]
        cell, head_h = 68, 24
        GW = pad * 2 + (len(paths) + 1) * cell
        GH = pad * 2 + head_h + len(picks) * cell
        g = Image.new("RGB", (GW, GH), "white")
        gd = ImageDraw.Draw(g)
        sf = _label_font(13)
        gd.text((pad, pad), "char xN", fill=(110, 110, 116), font=sf)
        for c in range(len(paths)):
            gd.text((pad + (c + 1) * cell + 6, pad), "set%d" % (c + 1),
                    fill=(110, 110, 116), font=sf)
        for r, ch in enumerate(picks):
            yy = pad + head_h + r * cell
            gd.text((pad, yy + cell // 3), "%s x%d" % (ch, cnt[ch]),
                    fill=(80, 80, 86), font=sf)
            for c, p in enumerate(paths, 1):
                try:
                    f = ImageFont.truetype(p, 46)
                    tile = Image.new("RGBA", (cell, cell), (0, 0, 0, 0))
                    ImageDraw.Draw(tile).text((6, 2), ch, font=f,
                                              fill=hwrender.INK + (255,))
                    g.paste(tile.convert("RGB"), (pad + c * cell, yy),
                            tile.getchannel("A"))
                except Exception:
                    pass
        g.save(grid)
        print("grid    -> %s  (%d chars x %d sets)" % (grid, len(picks), len(paths)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="字嗨多套手写字体 → 同字多形管线")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--text", help="正文（默认内置示例）")
        p.add_argument("--text-file", help="从 UTF-8 文件读正文")

    p = sub.add_parser("list", help="生成字嗨字表备份档")
    common(p)
    p.add_argument("--out", default=os.path.join(HERE, "out", "zihi"))
    p.add_argument("--count", type=int, default=4)
    p.add_argument("--family", default="MyHand")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("check", help="校验导出的多套 OTF")
    common(p)
    p.add_argument("--fonts", default=DEFAULT_FONT_DIR)
    p.add_argument("--expect", type=int, default=4)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("render", help="出同字多形对照样张")
    common(p)
    p.add_argument("--fonts", default=DEFAULT_FONT_DIR)
    p.add_argument("--out", default=os.path.join(HERE, "out", "variants_compare.png"))
    p.add_argument("--grid", default=os.path.join(HERE, "out", "variant_grid.png"))
    p.add_argument("--grid-chars", type=int, default=8)
    p.add_argument("--width", type=float, default=404)
    p.add_argument("--size", type=float, default=10.9)
    p.add_argument("--jitter", type=float, default=0.35)
    p.add_argument("--seed", type=int, default=20261118)
    p.set_defaults(func=cmd_render)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
