# -*- coding: utf-8 -*-
"""demo.py — 本地验证管线：空白 A4 + 一段三相电路计算，输出 PDF 与预览图。"""
import os
import fitz
from PIL import Image

import hwrender as H

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)
FONT = os.path.join(HERE, "fonts", "MaShanZheng-Regular-merged.ttf")

TEXT = """4.3.5 解：图4.03中，三角形联结的Z_b与星形联结的Z_a并联接入线电压380V的三相电源。
星形部分：相电压U相=380/√3≈220V，|Z_a|=√(3²+4²)=5Ω，相电流I=220/5=44A。
有功P_a=3×44²×3≈17.4kW，无功Q_a=3×44²×4≈23.2kvar（感性）。
三角形部分：相电压等于线电压380V，|Z_b|=√(8²+6²)=10Ω，相电流I_p=380/10=38A，
线电流I_l=√3×38≈65.8A。有功P_b=3×38²×8≈34.7kW，无功Q_b=−3×38²×6≈−26.0kvar（容性）。
总功率：P=17.4+34.7≈52.1kW，Q=23.2−26.0≈−2.8kvar，
S=√(P²+Q²)≈52.2kVA，I=S/(√3×380)≈79.2A，cosφ≈0.999。"""

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
    hand = H.Hand(FONT, size_pt=10.9, seed=20261118)
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
    pix = d[0].get_pixmap(dpi=120)
    pix.save(os.path.join(OUT, "demo_page.png"))
    # 局部放大 2 倍看字形细节
    full = Image.open(os.path.join(OUT, "demo_page.png"))
    box = (int(64 / 72 * 120) - 10, int(220 / 72 * 120) - 10,
           int(470 / 72 * 120), int(226 + h / 72 * 72 / 72 * 120) + 40)
    crop = full.crop(box)
    crop.resize((crop.width * 2 // 2, crop.height), Image.LANCZOS).save(os.path.join(OUT, "demo_crop.png"))
    print("preview ->", os.path.join(OUT, "demo_page.png"))


if __name__ == "__main__":
    main()
