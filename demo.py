# -*- coding: utf-8 -*-
"""demo.py — 本地验证管线：空白 A4 + 一段三相电路计算，输出 PDF 与预览图。"""
import os
import fitz
from PIL import Image

import hwrender as H
import symbols

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

TEXT = r"""4.3.5 解：图4.03中，三角形联结的$Z_b$与星形联结的$Z_a$并联接入线电压$380\,\mathrm{V}$的三相电源。
星形部分：相电压$U_{\mathrm{相}}=\frac{380}{\sqrt{3}}\approx 220\,\mathrm{V}$，$|Z_a|=\sqrt{3^2+4^2}=5\,\Omega$，相电流$I=\frac{220}{5}=44\,\mathrm{A}$。
有功$P_a=3\times 44^2\times 3\approx 17.4\,\mathrm{kW}$，无功$Q_a=3\times 44^2\times 4\approx 23.2\,\mathrm{kvar}$（感性）。
三角形部分：相电压等于线电压$380\,\mathrm{V}$，$|Z_b|=\sqrt{8^2+6^2}=10\,\Omega$，相电流$I_p=\frac{380}{10}=38\,\mathrm{A}$，
线电流$I_l=\sqrt{3}\times 38\approx 65.8\,\mathrm{A}$。有功$P_b=3\times 38^2\times 8\approx 34.7\,\mathrm{kW}$，无功$Q_b=-3\times 38^2\times 6\approx -26.0\,\mathrm{kvar}$（容性）。
总功率$P=\sum_{i=1}^{n}P_i\approx 52.1\,\mathrm{kW}$，$Q=23.2-26.0\approx -2.8\,\mathrm{kvar}$，
$S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，$I=\frac{S}{\sqrt{3}\times 380}\approx 79.2\,\mathrm{A}$，$\cos\varphi\approx 0.999$。"""

PRINT = """第四章 三相电路            4.3.5 三相负载的功率计算
在图 4.03 所示的三相电路中，已知线电压为 380V，负载 Z_a = 3 + j4 Ω，
Z_b = 8 − j6 Ω，试求电路的总有功功率、无功功率、视在功率及线电流。"""


def make_page():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)  # A4
    page.insert_text((64, 120), PRINT.split("\n")[0], fontname="china-s", fontsize=11)
    page.insert_text((64, 100), "", fontsize=11)
    # 模拟印刷正文
    y = 150
    for ln in PRINT.split("\n")[1:]:
        page.insert_text((64, y), ln, fontname="china-s", fontsize=10.5)
        y += 20
    return doc


def main():
    doc = make_page()
    page = doc[0]
    hand = symbols.make_han_hand(size_pt=H.DEFAULT_SIZE_PT, seed=20261118)
    img, w, h = hand.render_block(TEXT, width_pt=404)
    img.save(os.path.join(OUT, "block_raw.png"))

    import io
    buf = io.BytesIO(); img.save(buf, format="PNG")
    cap = fitz.Rect(64, 226, 64 + w, 226 + h)
    page.insert_image(cap, stream=buf.getvalue(), overlay=True, keep_proportion=False)
    doc.save(os.path.join(OUT, "demo_page.pdf"), deflate=True, garbage=3)
    doc.close()

    print("block: %.1f x %.1f pt, bottom=%.1f" % (w, h, 226 + h))

    d = fitz.open(os.path.join(OUT, "demo_page.pdf"))
    dpi = 120
    k = dpi / 72.0                      # pt -> px
    pix = d[0].get_pixmap(dpi=dpi)
    pix.save(os.path.join(OUT, "demo_page.png"))
    # 局部放大 2 倍看字形细节：坐标一律先换算成 px，不能 pt/px 混用
    full = Image.open(os.path.join(OUT, "demo_page.png"))
    box = (max(0, int(64 * k) - 10), max(0, int(220 * k) - 10),
           min(full.width, int(470 * k)), min(full.height, int((226 + h) * k) + 10))
    crop = full.crop(box)
    crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS).save(
        os.path.join(OUT, "detail2x.png"))
    print("preview ->", os.path.join(OUT, "demo_page.png"),
          "| detail ->", os.path.join(OUT, "detail2x.png"))


if __name__ == "__main__":
    main()
