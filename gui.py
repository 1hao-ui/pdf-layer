# -*- coding: utf-8 -*-
"""gui.py — PDF-Layer 可视化控制台（tkinter，零额外依赖）。

布局
----
┌─────────────┬──────────────────────────────┐
│ 参数面板     │  预览（A4 页面 + 手写层）      │
│ 字体/字号/   │                              │
│ 行距/抖动/   │                              │
│ 墨色/种子    │                              │
├─────────────┴──────────────────────────────┤
│ 控制台：日志 + 命令行（既能调参，也能写意见）  │
└────────────────────────────────────────────┘

控制台用法
----------
    直接敲参数        size=13.2   line=24   jitter=0.8   seed=1
                     ink=16,16,18          width=404     font=马善政
    动作              render / save png / save pdf / reset / help / clear
    其他任何文字       记为一条「修改意见」，追加到 feedback.md

这样你可以一边看图一边把问题写下来，我下次直接读 feedback.md。
"""
import os
import re
import sys
import threading
import traceback
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import hwrender as H          # noqa: E402
import symbols                # noqa: E402
import fontcal                # noqa: E402
from PIL import Image         # noqa: E402

FEEDBACK = os.path.join(HERE, "feedback.md")
OUT_DIR = os.path.join(HERE, "out")

# ---------------------------------------------------------------- 可选字体
FONT_LABELS = [
    ("马善政楷书（毛笔，正文默认）", "MaShanZheng-Regular-merged.ttf"),
    ("龙藏体（硬笔，数字/拉丁默认）", "LongCang-Regular-merged.ttf"),
    ("志莽行书", "ZhiMangXing-Regular-merged.ttf"),
    ("刘建毛草", "LiuJianMaoCao-Regular-merged.ttf"),
    ("ZTi 我的手写·字嗨 4 套", "ZTi/ZTi-01.otf"),
    ("ZTi 我的手写·真迹扫描 4 套", "ZTi/ZTi-Scan-01.ttf"),
]
MATH_FONT_DEFAULT = "LongCang-Regular-merged.ttf"

SAMPLE_TEXT = r"""4.3.5 解：图4.03中，三角形联结的$Z_b$与星形联结的$Z_a$并联接入线电压$380\,\mathrm{V}$的三相电源。
星形部分：相电压$U_{\mathrm{相}}=\frac{380}{\sqrt{3}}\approx 220\,\mathrm{V}$，$|Z_a|=\sqrt{3^2+4^2}=5\,\Omega$，相电流$I=\frac{220}{5}=44\,\mathrm{A}$。
有功$P_a=3\times 44^2\times 3\approx 17.4\,\mathrm{kW}$，无功$Q_a=3\times 44^2\times 4\approx 23.2\,\mathrm{kvar}$（感性）。
总功率$P=\sum_{i=1}^{n}P_i\approx 52.1\,\mathrm{kW}$，$S=\sqrt{P^2+Q^2}\approx 52.2\,\mathrm{kVA}$，$\cos\varphi\approx 0.999$。"""


def _font_path(name):
    return os.path.join(HERE, "fonts", name)


def _variant_fonts(main_name=None):
    """ZTi 多套字库。

    · 主字体是普通字体：把 fonts/ZTi 下 8 套全给（毛笔/硬笔混排会被
      风格护栏挡住并退回单套，保持原行为）。
    · 主字体本身是 ZTi：只轮换**同组**另外 3 套——实测字嗨（ZTi-0N.otf）
      与真迹扫描（ZTi-Scan-0N.ttf）墨迹占比差 1.4 倍，护栏会拒绝混编；
      分成两组各转各的，同字多形才真正生效。
    """
    d = os.path.join(HERE, "fonts", "ZTi")
    if not os.path.isdir(d):
        return []
    files = [n for n in sorted(os.listdir(d)) if n.endswith((".otf", ".ttf"))]
    if main_name and main_name.replace("\\", "/").startswith("ZTi/"):
        own = os.path.basename(main_name.replace("\\", "/"))
        want_scan = "Scan" in own
        group = [n for n in files if ("Scan" in n) == want_scan]
        return [os.path.join(d, n) for n in group if n != own]
    return [os.path.join(d, n) for n in files]


class App:
    def __init__(self, root):
        import tkinter as tk
        from tkinter import ttk
        self.tk, self.ttk = tk, ttk
        self.root = root
        root.title("PDF-Layer 手写层控制台")
        root.geometry("1360x820")
        root.minsize(1080, 660)
        # 居中偏上，避免底部按钮被任务栏挡住
        root.update_idletasks()
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        w, h = 1360, min(820, sh - 120)
        root.geometry("%dx%d+%d+%d" % (w, h, (sw - w) // 2, max(20, (sh - h) // 3)))

        # ---- 渲染参数（唯一真相源，控制台和控件都改它）----
        self.cfg = {
            "font": FONT_LABELS[0][1],
            "size": H.DEFAULT_SIZE_PT,
            "line": H.DEFAULT_LINE_PT,
            "jitter": 1.0,
            "seed": 20261118,
            "ink": H.INK,
            "width": 404.0,
            "variants": False,
            "page": True,
            # 书写区域（pt，页面坐标）：可以在预览上拖框直接划定。
            # 默认比正文列宽一些（A4 左右各 56pt 边距），避免右侧留一大片白。
            "area": [56.0, 180.0, 539.0, 700.0],
            "area_mode": False,       # 为 True 时在预览上拖框 = 划定书写区域
        }
        self.last_image = None
        self.last_size = (0.0, 0.0)
        self._photo = None

        self._build_ui()
        self.log("控制台就绪。输入 help 查看命令；直接写文字会记入 feedback.md。")
        self.root.after(200, self.render_async)

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        tk, ttk = self.tk, self.ttk
        outer = ttk.Frame(self.root, padding=6)
        outer.pack(fill="both", expand=True)

        paned = ttk.PanedWindow(outer, orient="horizontal")
        paned.pack(fill="both", expand=True)

        # 左：参数（固定较宽，避免字体名被截断）
        left = ttk.Frame(paned, padding=4, width=340)
        paned.add(left, weight=0)
        self._build_params(left)

        # 右：预览
        right = ttk.Frame(paned, padding=4)
        paned.add(right, weight=1)
        self._build_preview(right)

        # 底：控制台 + 意见
        bottom = ttk.LabelFrame(outer, text="控制台（可调参 / 提意见）", padding=4)
        bottom.pack(fill="both", expand=False, pady=(6, 0))
        self._build_console(bottom)

    def _build_params(self, parent):
        tk, ttk = self.tk, self.ttk
        box = ttk.Frame(parent)
        box.pack(fill="both", expand=True)

        row = 0
        ttk.Label(box, text="字体").grid(row=row, column=0, sticky="w", pady=2)
        self.font_var = tk.StringVar(value=FONT_LABELS[0][0])
        cb = ttk.Combobox(box, textvariable=self.font_var, width=26, state="readonly",
                          values=[l for l, _ in FONT_LABELS])
        cb.grid(row=row, column=1, sticky="ew", pady=2)
        cb.bind("<<ComboboxSelected>>", lambda e: self._on_font())
        row += 1

        self.var_size = tk.DoubleVar(value=self.cfg["size"])
        self.var_line = tk.DoubleVar(value=self.cfg["line"])
        self.var_jitter = tk.DoubleVar(value=self.cfg["jitter"])
        self.var_seed = tk.IntVar(value=self.cfg["seed"])
        self.var_width = tk.DoubleVar(value=self.cfg["width"])
        self.var_ink = tk.StringVar(value=",".join(str(c) for c in self.cfg["ink"]))
        self.var_variants = tk.BooleanVar(value=False)

        for label, var, lo, hi, step in (
            ("字号 pt", self.var_size, 6, 30, 0.2),
            ("行距 pt", self.var_line, 8, 60, 0.5),
            ("抖动", self.var_jitter, 0, 2, 0.05),
            ("列宽 pt", self.var_width, 100, 520, 2),
        ):
            ttk.Label(box, text=label).grid(row=row, column=0, sticky="w", pady=2)
            sp = ttk.Spinbox(box, textvariable=var, from_=lo, to=hi, increment=step,
                             width=10, command=self.render_async)
            sp.grid(row=row, column=1, sticky="ew", pady=2)
            sp.bind("<Return>", lambda e: self.render_async())
            row += 1

        ttk.Label(box, text="种子").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(box, textvariable=self.var_seed, width=12).grid(
            row=row, column=1, sticky="ew", pady=2)
        row += 1

        ttk.Label(box, text="墨色 R,G,B").grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(box, textvariable=self.var_ink, width=12).grid(
            row=row, column=1, sticky="ew", pady=2)
        row += 1

        ttk.Checkbutton(box, text="启用 ZTi 多套字库（风格不符会被护栏拒绝）",
                        variable=self.var_variants,
                        command=self.render_async).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(6, 2))
        row += 1

        self.var_newsym = tk.BooleanVar(value=symbols.available())
        ttk.Checkbutton(box, text="符号用新手写字库 ZTi-Sym（4 套加权）",
                        variable=self.var_newsym,
                        command=self.render_async).grid(
            row=row, column=0, columnspan=2, sticky="w", pady=(2, 2))
        row += 1

        # ---- 书写区域 ----
        ttk.Separator(box, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=(6, 2))
        row += 1
        area_head = ttk.Frame(box)
        area_head.grid(row=row, column=0, columnspan=2, sticky="ew")
        ttk.Label(area_head, text="书写区域（pt）").pack(side="left")
        ttk.Button(area_head, text="在预览上拖框划定",
                   command=self._toggle_area_mode).pack(side="right")
        row += 1
        self.var_area = [tk.StringVar(value="%.0f" % v) for v in self.cfg["area"]]
        af = ttk.Frame(box)
        af.grid(row=row, column=0, columnspan=2, sticky="ew", pady=2)
        for i, name in enumerate(("左", "上", "右", "下")):
            ttk.Label(af, text=name).pack(side="left")
            e = ttk.Entry(af, textvariable=self.var_area[i], width=5)
            e.pack(side="left", padx=(0, 6))
            e.bind("<Return>", lambda ev: self.render_async())
        row += 1
        ttk.Label(box, text="提示：拖框模式下在预览上按住左键拉出矩形",
                  foreground="#666").grid(row=row, column=0, columnspan=2, sticky="w")
        row += 1

        ttk.Separator(box, orient="horizontal").grid(
            row=row, column=0, columnspan=2, sticky="ew", pady=6)
        row += 1

        ttk.Label(box, text="正文（支持 $...$ 公式）").grid(
            row=row, column=0, columnspan=2, sticky="w")
        row += 1
        self.text = tk.Text(box, width=30, height=16, wrap="word")
        self.text.grid(row=row, column=0, columnspan=2, sticky="nsew")
        self.text.insert("1.0", SAMPLE_TEXT)
        box.rowconfigure(row, weight=2)
        row += 1

        btns = ttk.Frame(box)
        btns.grid(row=row, column=0, columnspan=2, sticky="ew", pady=6)
        ttk.Button(btns, text="渲染 (Ctrl+Enter)", command=self.render_async).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(btns, text="导出 PNG", command=self.save_png).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(btns, text="导出 PDF", command=self.save_pdf).pack(
            side="left", expand=True, fill="x", padx=1)
        box.columnconfigure(1, weight=1)
        self.root.bind("<Control-Return>", lambda e: self.render_async())

    def _build_preview(self, parent):
        tk, ttk = self.tk, self.ttk
        bar = ttk.Frame(parent)
        bar.pack(fill="x")
        self.status = ttk.Label(bar, text="", anchor="w")
        self.status.pack(side="left", fill="x", expand=True)
        ttk.Label(bar, text="缩放").pack(side="left", padx=(8, 2))
        self.var_zoom = tk.DoubleVar(value=0.0)   # 0 = 自动适应
        zs = ttk.Spinbox(bar, textvariable=self.var_zoom, from_=0, to=4,
                         increment=0.1, width=6, command=self._redraw)
        zs.pack(side="left")
        zs.bind("<Return>", lambda e: self._redraw())
        ttk.Label(bar, text="（0=自动）").pack(side="left", padx=(2, 8))
        ttk.Button(bar, text="适应窗口", command=lambda: (
            self.var_zoom.set(0), self._redraw())).pack(side="left")

        self.canvas = tk.Canvas(parent, background="#9a9a9a", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        # 滚轮缩放（以光标为中心）、左键拖动平移
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.canvas.bind("<ButtonPress-1>", self._on_drag_start)
        self.canvas.bind("<B1-Motion>", self._on_drag_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_drag_end)
        self.canvas.bind("<Double-Button-1>", lambda e: self._reset_view())
        self._drag = None          # (起点x, 起点y, 起始偏移x, 起始偏移y)
        self._offset = [0, 0]      # 平移量（屏幕像素）
        self._last_zoom = 1.0
        self._disp_img = None
        self._area_drag = None

    def _build_console(self, parent):
        tk, ttk = self.tk, self.ttk
        wrap = ttk.Frame(parent)
        wrap.pack(fill="both", expand=True)

        # 左：日志  右：意见速记
        split = ttk.PanedWindow(wrap, orient="horizontal")
        split.pack(fill="both", expand=True)

        logbox = ttk.Frame(split)
        split.add(logbox, weight=3)
        self.log_text = tk.Text(logbox, height=7, wrap="word", state="disabled",
                                background="#1e1e1e", foreground="#d4d4d4",
                                insertbackground="#d4d4d4")
        self.log_text.pack(fill="both", expand=True)
        line = ttk.Frame(logbox)
        line.pack(fill="x", pady=(4, 0))
        ttk.Label(line, text=">").pack(side="left")
        self.entry = ttk.Entry(line)
        self.entry.pack(side="left", fill="x", expand=True, padx=4)
        self.entry.bind("<Return>", lambda e: self._on_command())
        ttk.Button(line, text="执行", command=self._on_command).pack(side="left")

        notebox = ttk.Frame(split)
        split.add(notebox, weight=2)
        ttk.Label(notebox, text="修改意见（点「记录」写入 feedback.md）").pack(anchor="w")
        self.note = tk.Text(notebox, height=6, wrap="word")
        self.note.pack(fill="both", expand=True)
        nb = ttk.Frame(notebox)
        nb.pack(fill="x", pady=(4, 0))
        ttk.Button(nb, text="记录意见", command=self._record_note).pack(
            side="left", expand=True, fill="x", padx=1)
        ttk.Button(nb, text="打开 feedback.md", command=self._open_feedback).pack(
            side="left", expand=True, fill="x", padx=1)

    def _record_note(self):
        text = self.note.get("1.0", "end").strip()
        if not text:
            self.log("意见为空")
            return
        self._record_feedback(text)
        self.note.delete("1.0", "end")

    def _open_feedback(self):
        import subprocess
        try:
            if not os.path.exists(FEEDBACK):
                self._record_feedback("（初始化）")
            os.startfile(FEEDBACK)  # Windows
        except Exception:
            try:
                subprocess.Popen(["notepad.exe", FEEDBACK])
            except Exception:
                self.log("打不开 feedback.md，路径：%s" % FEEDBACK)

    # ------------------------------------------------------------ 日志
    def log(self, msg):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", msg.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------ 参数
    def _on_font(self):
        label = self.font_var.get()
        for lab, fn in FONT_LABELS:
            if lab == label:
                self.cfg["font"] = fn
                break
        if "ZTi/" in self.cfg["font"]:
            # 选自己的手写字库时默认开启同字多形（不想用可手动取消勾选）
            self.var_variants.set(True)
        self.render_async()

    def _pull_cfg(self):
        """把控件里的值同步进 self.cfg。"""
        self.cfg["size"] = float(self.var_size.get())
        self.cfg["line"] = float(self.var_line.get())
        self.cfg["jitter"] = float(self.var_jitter.get())
        self.cfg["seed"] = int(self.var_seed.get())
        self.cfg["width"] = float(self.var_width.get())
        self.cfg["variants"] = bool(self.var_variants.get())
        raw = self.var_ink.get().replace("，", ",")
        parts = [int(p) for p in raw.split(",") if p.strip()]
        if len(parts) == 3:
            self.cfg["ink"] = tuple(max(0, min(255, p)) for p in parts)

    def _push_cfg(self):
        """把 self.cfg 写回控件（控制台改参数后调用）。"""
        for lab, fn in FONT_LABELS:
            if fn == self.cfg["font"]:
                self.font_var.set(lab)
        self.var_size.set(self.cfg["size"])
        self.var_line.set(self.cfg["line"])
        self.var_jitter.set(self.cfg["jitter"])
        self.var_seed.set(self.cfg["seed"])
        self.var_width.set(self.cfg["width"])
        self.var_ink.set(",".join(str(c) for c in self.cfg["ink"]))
        self.var_variants.set(self.cfg["variants"])

    # ------------------------------------------------------------ 渲染
    def render_async(self):
        self._pull_cfg()
        threading.Thread(target=self._render_worker, daemon=True).start()

    def _render_worker(self):
        try:
            text = self.text.get("1.0", "end").rstrip()
            if not text.strip():
                self.log("正文为空，跳过渲染")
                return
            font_path = _font_path(self.cfg["font"])
            math_path = _font_path(MATH_FONT_DEFAULT)
            # ZTi 当主字体时，符号/缺字兜底也用硬笔的龙藏体，
            # 跟自己的硬笔手写风格一致（默认会挑到毛笔的马善政）。
            sym_path = (_font_path(MATH_FONT_DEFAULT)
                        if "ZTi/" in self.cfg["font"] else None)
            if self.var_newsym.get() and symbols.available():
                # 汉字走中文字体，数字/拉丁/希腊/符号走 ZTi-Sym 4 套加权
                hand = symbols.make_hand(
                    font_path,
                    size_pt=float(self.cfg["size"]),
                    ink=tuple(self.cfg["ink"]),
                    jitter=float(self.cfg["jitter"]),
                    seed=int(self.cfg["seed"]),
                )
            else:
                hand = H.Hand(
                    font_path,
                    size_pt=float(self.cfg["size"]),
                    ink=tuple(self.cfg["ink"]),
                    jitter=float(self.cfg["jitter"]),
                    seed=int(self.cfg["seed"]),
                    math_font_path=math_path,
                    symbol_font_path=sym_path,
                    variant_fonts=(_variant_fonts(self.cfg["font"])
                                   if self.cfg["variants"] else None),
                )
            # 书写区域决定正文宽度（在预览上拖框划定，或用四个数值手填）
            area = [float(v.get()) for v in self.var_area]
            left, top, right, bottom = area[0], area[1], area[2], area[3]
            width_pt = max(60.0, right - left)
            # 行距用参数传进去（不再改模块常量，避免多线程互相影响）
            img, w_pt, h_pt = hand.render_block(
                text, width_pt=width_pt, line_pt=float(self.cfg["line"]))
            note = getattr(hand, "style_note", "") or ""
            self.last_image = img
            self.last_size = (w_pt, h_pt)
            self.root.after(0, lambda: self._on_rendered(img, w_pt, h_pt, note))
        except Exception:
            tb = traceback.format_exc()
            self.root.after(0, lambda: self.log("渲染失败：\n" + tb))

    def _on_rendered(self, img, w_pt, h_pt, note):
        self.status.configure(text="块尺寸 %.1f × %.1f pt   %s" % (w_pt, h_pt, note))
        self.log("渲染完成：%.1f × %.1f pt %s" % (w_pt, h_pt, ("｜" + note) if note else ""))
        self._redraw()

    # ------------------------------------------------------------ 缩放/平移
    def _on_wheel(self, event):
        """滚轮缩放：以光标位置为中心，手感比「改数值再重算」自然得多。"""
        cur = float(self.var_zoom.get())
        if cur <= 0.001:                      # 当前是自动，先固化成实际值
            cur = self._last_zoom or 1.0
        factor = 1.15 if event.delta > 0 else 1 / 1.15
        new = max(0.05, min(8.0, cur * factor))
        # 让光标下的那个点保持不动
        cx, cy = self.canvas.canvasx(event.x), self.canvas.canvasy(event.y)
        self.var_zoom.set(round(new, 3))
        self._redraw()
        # 重绘后按比例补偿偏移
        if cur > 0:
            k = new / cur
            self._offset[0] = int((self._offset[0] + cx) * k - cx)
            self._offset[1] = int((self._offset[1] + cy) * k - cy)
            self._redraw()

    def _toggle_area_mode(self):
        """切换「拖框划定书写区域」模式。

        进入时把页面缩放到整页可见——否则页面被放得很大，
        拖半天也只框到很小一块（实测拖 400 屏幕像素只覆盖 76pt）。
        """
        self.cfg["area_mode"] = not self.cfg["area_mode"]
        if self.cfg["area_mode"]:
            self.canvas.configure(cursor="crosshair")
            self.cfg["page"] = True          # 拖框必须在整页视图下进行
            ch = max(50, self.canvas.winfo_height())
            cw = max(50, self.canvas.winfo_width())
            page_w, page_h = 595 * H.SCALE, 842 * H.SCALE
            self.var_zoom.set(round(min(cw / page_w, ch / page_h) * 0.95, 3))
            self._offset = [0, 0]
            self.log("拖框模式：开（已缩放到整页，按住左键拉出矩形）")
        else:
            self.canvas.configure(cursor="")
            self.log("拖框模式：关")
        self._redraw()

    def _screen_to_page(self, x, y):
        """画布坐标 -> 页面 pt 坐标。

        链条：画布像素 ->（减偏移、除 zoom）-> 显示图坐标 ->（除 SCALE）-> pt。
        显示图可能是「整页」也可能是「块」；拖框只在页面模式下有意义，
        块模式下按块左上角推算。
        """
        zoom = self._last_zoom or 1.0
        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        img = self._disp_img
        if img is None:
            return None
        disp_w = img.width * zoom
        disp_h = img.height * zoom
        ox = cw // 2 + self._offset[0] - disp_w / 2
        oy = ch // 2 + self._offset[1] - disp_h / 2
        gx = (x - ox) / zoom          # 显示图内的像素
        gy = (y - oy) / zoom
        if self.cfg["page"]:
            return gx / H.SCALE, gy / H.SCALE
        # 块模式：块图左上角对应区域左上角
        area = [float(v.get()) for v in self.var_area]
        return area[0] + gx / H.SCALE, area[1] + gy / H.SCALE

    def _on_drag_start(self, event):
        if self.cfg["area_mode"]:
            self._area_drag = (event.x, event.y)
            return
        self._drag = (event.x, event.y, self._offset[0], self._offset[1])
        self.canvas.configure(cursor="fleur")

    def _on_drag_move(self, event):
        if self.cfg["area_mode"]:
            if not getattr(self, "_area_drag", None):
                return
            x0, y0 = self._area_drag
            self.canvas.delete("area")
            self.canvas.create_rectangle(x0, y0, event.x, event.y,
                                         outline="#ff5252", width=2, tags="area")
            return
        if not self._drag:
            return
        x0, y0, ox, oy = self._drag
        self._offset[0] = ox + (event.x - x0)
        self._offset[1] = oy + (event.y - y0)
        self._redraw()

    def _on_drag_end(self, event):
        if not self.cfg["area_mode"]:
            self.canvas.configure(cursor="")
            return
        start = getattr(self, "_area_drag", None)
        self._area_drag = None
        if not start:
            return
        a = self._screen_to_page(*start)
        b = self._screen_to_page(event.x, event.y)
        if not a or not b:
            return
        left, right = sorted((a[0], b[0]))
        top, bottom = sorted((a[1], b[1]))
        # 夹到页面范围内（A4 = 595 x 842 pt），并留 8pt 边
        # 右/下边界放到页面边缘（595/842），不再强制留 8pt。
        # 之前夹到 587，用户拖到最右也只到 587，页面右边永远有一道白。
        left = max(8.0, min(595.0, left))
        right = max(left + 60.0, min(595.0, right))
        top = max(8.0, min(842.0, top))
        bottom = max(top + 40.0, min(842.0, bottom))
        if right - left < 60 or bottom - top < 40:      # 太小当作误触
            self.log("区域太小（宽 %.0f 高 %.0f），已忽略" % (right - left, bottom - top))
            return
        self.cfg["area"] = [round(left, 1), round(top, 1),
                            round(right, 1), round(bottom, 1)]
        for i, v in enumerate(self.cfg["area"]):
            self.var_area[i].set("%.0f" % v)
        self.cfg["area_mode"] = False
        self.canvas.configure(cursor="")
        self.canvas.delete("area")
        self.log("书写区域：左 %.0f 上 %.0f 右 %.0f 下 %.0f（宽 %.0f 高 %.0f pt）"
                 % (left, top, right, bottom, right - left, bottom - top))
        self.render_async()

    def _reset_view(self):
        self._offset = [0, 0]
        self.var_zoom.set(0)
        self._redraw()
        self.canvas.configure(cursor="")

    def _redraw(self):
        if self.last_image is None:
            return
        from PIL import ImageTk
        cw = max(50, self.canvas.winfo_width())
        ch = max(50, self.canvas.winfo_height())
        img = self.last_image
        if self.cfg["page"]:
            # 贴到 A4 白纸上。**必须用和手写块相同的 SCALE**（4 px/pt）：
            # 之前按 2 px/pt 建页面，把 4x 的块贴上去等于放大一倍，
            # 右侧 28% 会溢出页面被切掉——这就是「显示不完整、明显截断」的原因。
            k = H.SCALE
            page = Image.new("RGBA", (int(595 * k), int(842 * k)), (255, 255, 255, 255))
            area = [float(v.get()) for v in self.var_area]
            page.alpha_composite(img, (int(area[0] * k), int(area[1] * k)))
            # 画出书写区域边框，便于确认位置
            from PIL.ImageDraw import Draw as _Draw
            _Draw(page).rectangle([int(area[0] * k), int(area[1] * k),
                                 int(area[2] * k), int(area[3] * k)],
                                outline=(255, 120, 120, 160), width=2)
            img = page.convert("RGB")
        zoom = float(self.var_zoom.get()) if hasattr(self, "var_zoom") else 0.0
        if zoom <= 0.001:
            # 自动：以「手写块本身」为准算缩放，页面模式下再限制不超过页面可视高度。
            # 之前按整页算，4x 页面（2380×3368）会被缩到 17%，字小到看不清。
            if self.cfg["page"] and self.last_image is not None:
                bw, bh = self.last_image.size
                fit_block = min(cw / bw, ch / bh) * 0.9
                page_h = 842 * H.SCALE
                fit_page = ch / page_h
                zoom = min(fit_block, fit_page * 2.2)
            else:
                zoom = min(cw / img.width, ch / img.height)
        zoom = max(0.05, min(8.0, zoom))
        self._last_zoom = zoom
        self._disp_img = img          # 供 _screen_to_page 换算坐标
        disp = img.resize((max(1, int(img.width * zoom)), max(1, int(img.height * zoom))),
                          Image.LANCZOS)
        self._photo = ImageTk.PhotoImage(disp)
        self.canvas.delete("all")
        # 平移量叠加到居中位置（滚轮缩放/左键拖动改的就是这个偏移）
        self.canvas.create_image(cw // 2 + self._offset[0],
                                 ch // 2 + self._offset[1], image=self._photo)
        self.canvas.create_text(8, 8, anchor="nw", fill="#f0f0f0",
                                text="%d%%   滚轮缩放 · 左键拖动 · 双击复位" % int(zoom * 100))

    # ------------------------------------------------------------ 导出
    def _ensure_out(self):
        os.makedirs(OUT_DIR, exist_ok=True)

    def save_png(self):
        if self.last_image is None:
            self.log("还没有渲染结果")
            return
        self._ensure_out()
        path = os.path.join(OUT_DIR, "gui_%s.png" % datetime.now().strftime("%H%M%S"))
        bg = Image.new("RGBA", self.last_image.size, (255, 255, 255, 255))
        bg.alpha_composite(self.last_image)
        bg.convert("RGB").save(path)
        self.log("已导出 PNG：%s" % path)

    def save_pdf(self):
        if self.last_image is None:
            self.log("还没有渲染结果")
            return
        self._ensure_out()
        path = os.path.join(OUT_DIR, "gui_%s.pdf" % datetime.now().strftime("%H%M%S"))
        try:
            import fitz
        except Exception:
            self.log("导出 PDF 需要 PyMuPDF（本机 Python 版本装不上）；"
                     "已改为导出 PNG，可用 demo.py 在有 fitz 的环境出 PDF")
            self.save_png()
            return
        try:
            doc = fitz.open()
            page = doc.new_page(width=595, height=842)
            import io
            buf = io.BytesIO()
            self.last_image.save(buf, format="PNG")
            w_pt, h_pt = self.last_size
            page.insert_image(fitz.Rect(64, 226, 64 + w_pt, 226 + h_pt),
                              stream=buf.getvalue(), overlay=True, keep_proportion=False)
            doc.save(path, deflate=True, garbage=3)
            doc.close()
            self.log("已导出 PDF：%s" % path)
        except Exception:
            self.log("导出 PDF 失败：\n" + traceback.format_exc())

    # ------------------------------------------------------------ 控制台
    def _on_command(self):
        raw = self.entry.get().strip()
        if not raw:
            return
        self.entry.delete(0, "end")
        self.log("> " + raw)
        if self._try_command(raw):
            return
        # 不是命令 -> 记为修改意见
        self._record_feedback(raw)

    def _try_command(self, raw):
        low = raw.lower()
        if low in ("help", "?", "？"):
            self.log("命令：size=  line=  jitter=  seed=  width=  ink=r,g,b  font=名字\n"
                     "      variants=on|off  page=on|off\n"
                     "      render | save png | save pdf | reset | clear\n"
                     "其他文字会追加到 feedback.md")
            return True
        if low == "render":
            self.render_async()
            return True
        if low == "save png":
            self.save_png()
            return True
        if low == "save pdf":
            self.save_pdf()
            return True
        if low == "clear":
            self.log_text.configure(state="normal")
            self.log_text.delete("1.0", "end")
            self.log_text.configure(state="disabled")
            return True
        if low == "reset":
            self.cfg.update({"size": H.DEFAULT_SIZE_PT, "line": H.DEFAULT_LINE_PT,
                             "jitter": 1.0, "seed": 20261118, "ink": H.INK,
                             "width": 404.0, "variants": False})
            self._push_cfg()
            self.log("参数已重置")
            self.render_async()
            return True

        m = re.match(r"^([a-zA-Z_]+)\s*=\s*(.+)$", raw)
        if m:
            key, val = m.group(1).lower(), m.group(2).strip()
            try:
                if key == "size":
                    self.cfg["size"] = float(val)
                elif key == "line":
                    self.cfg["line"] = float(val)
                elif key == "jitter":
                    self.cfg["jitter"] = float(val)
                elif key == "seed":
                    self.cfg["seed"] = int(val)
                elif key == "width":
                    self.cfg["width"] = float(val)
                elif key == "ink":
                    parts = [int(p) for p in val.replace("，", ",").split(",") if p.strip()]
                    if len(parts) != 3:
                        raise ValueError("墨色要写成 R,G,B")
                    self.cfg["ink"] = tuple(max(0, min(255, p)) for p in parts)
                elif key == "variants":
                    self.cfg["variants"] = val.lower() in ("on", "1", "true", "yes", "开")
                elif key == "page":
                    self.cfg["page"] = val.lower() in ("on", "1", "true", "yes", "开")
                elif key == "font":
                    hit = [fn for lab, fn in FONT_LABELS if val in lab or val == fn]
                    if not hit:
                        raise ValueError("未知字体，可选：%s"
                                         % "、".join(l for l, _ in FONT_LABELS))
                    self.cfg["font"] = hit[0]
                else:
                    return False
                self._push_cfg()
                self.log("已设置 %s = %s" % (key, val))
                if key != "page":
                    self.render_async()
                else:
                    self._redraw()
                return True
            except Exception as e:
                self.log("参数错误：%s" % e)
                return True
        return False

    def _record_feedback(self, text):
        try:
            os.makedirs(os.path.dirname(FEEDBACK), exist_ok=True)
            new = not os.path.exists(FEEDBACK)
            with open(FEEDBACK, "a", encoding="utf-8") as fp:
                if new:
                    fp.write("# PDF-Layer 修改意见（控制台记录）\n\n")
                fp.write("- `%s`  %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), text))
            self.log("已记录到 feedback.md")
        except Exception:
            self.log("写 feedback.md 失败：\n" + traceback.format_exc())


def main():
    try:
        import tkinter as tk
    except Exception:
        print("没有 tkinter，无法启动图形界面。")
        return 1
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
