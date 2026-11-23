# -*- coding: utf-8 -*-
"""tests/render_baseline.py — 渲染回归基线。

用途：任何模块改动前后各跑一次，比对 JSON 里的几何尺寸与像素哈希。
哈希一致 = 渲染链路未受影响（覆盖：字库解析 → 校准 → 排版 → 公式层）。

用法（在 源码/pdf-layer 下）：
    py -3 tests/render_baseline.py save     # 生成/覆盖基线
    py -3 tests/render_baseline.py check    # 与基线比对，不一致则退出码 1

基线产物：tests/baseline/baseline.json + 两张 PNG（便于肉眼比对）。
"""
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
OUT = os.path.join(HERE, "baseline")
STORE = os.path.join(OUT, "baseline.json")

sys.path.insert(0, REPO)
os.chdir(REPO)

TEXT = "三相电路中，相电压等于线电压除以根号三。电阻 R=10Ω，电流 I=220/5=44A。"
FORMULA = (r"视在功率 $S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，"
           r"功率因数 $\cos\varphi\approx 0.999$。")


def build():
    """渲染两段样文，返回可序列化的度量。"""
    import symbols

    rep = {}
    hand = symbols.make_han_hand()
    rep["style_note"] = getattr(hand, "style_note", None)
    rep["main_font"] = os.path.basename(hand.font_path)
    rep["math_font"] = os.path.basename(getattr(hand, "math_font_path", "") or "")
    # 字库解析结果：决定了走 ZTi 新库还是回退老字体。
    # 只记目录名不记绝对路径，换机器/挪目录不会让基线失效。
    han_dir, sym_dir = symbols.han_dir(), symbols.sym_dir()
    rep["han_dir"] = os.path.basename(han_dir) if han_dir else "<缺失>"
    rep["sym_dir"] = os.path.basename(sym_dir) if sym_dir else "<缺失>"

    import fontcal
    for tag, d in (("cal_han", han_dir), ("cal_sym", sym_dir)):
        path = os.path.join(d, "pass2.ttf") if d else ""
        rep[tag] = fontcal.params_for(path) if path and os.path.exists(path) else "<缺失>"

    for tag, text in (("text", TEXT), ("formula", FORMULA)):
        img, w, h = hand.render_block(text, width_pt=404)
        rep[tag] = {"w": round(w, 3), "h": round(h, 3), "size": list(img.size),
                    "mode": img.mode,
                    "sha": hashlib.sha256(img.tobytes()).hexdigest()}
        os.makedirs(OUT, exist_ok=True)
        img.save(os.path.join(OUT, "%s.png" % tag))
    return rep


def flatten(d, prefix=""):
    """把嵌套度量拍平成 {路径: 值}，便于逐项比对。"""
    flat = {}
    for k, v in d.items():
        key = "%s.%s" % (prefix, k) if prefix else k
        if isinstance(v, dict):
            flat.update(flatten(v, key))
        elif isinstance(v, list):
            flat[key] = tuple(v)
        else:
            flat[key] = v
    return flat


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "check"
    cur = build()
    os.makedirs(OUT, exist_ok=True)

    if action == "save":
        with open(STORE, "w", encoding="utf-8") as fp:
            json.dump(cur, fp, ensure_ascii=False, indent=2)
        print("基线已保存 ->", STORE)
        print(json.dumps(cur, ensure_ascii=False, indent=2))
        return 0

    if not os.path.exists(STORE):
        print("没有基线文件，先跑： py -3 tests/render_baseline.py save")
        return 2

    with open(STORE, encoding="utf-8") as fp:
        base = json.load(fp)
    a, b = flatten(base), flatten(cur)
    diffs = [(k, a.get(k, "<缺>"), b.get(k, "<缺>"))
             for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]

    if not diffs:
        print("PASS  渲染与基线完全一致（几何 + 像素哈希）")
        return 0
    print("FAIL  与基线有 %d 处差异：" % len(diffs))
    for k, old, new in diffs:
        print("  %-24s 基线=%s  当前=%s" % (k, old, new))
    print("\n若这是预期的改动，跑 save 覆盖基线，并在提交信息里说明原因。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
