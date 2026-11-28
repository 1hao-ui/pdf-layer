# -*- coding: utf-8 -*-
"""fontlab_gui.py — 字库工作台（自建字库的图形界面）。

页签对应真实流程：

    字库        现有字库列表：启用 / 停用 / 重新校准 / 删除
    新建 / 补字  已有字体文件可直接上传；没有就选字 → 出模板 → 手写 → 扫描
    上传与识别  上传扫描图/PDF → 自动对齐切字 → 自动参数 → 逐字微调
    预览与生成  1:1 效果示例（与当前字库并排）→ 生成正式字库并启用

另外提供 ``import_dialog()``：主界面顶栏「字库 → 上传字体文件」用的就是它。

界面只做三件事：收集参数、调 ``fontlib`` 里的函数、把结果显示出来。
真正的逻辑都在 ``fontlib/``，可以无头测试。
"""
from __future__ import annotations

import json
import os
import threading

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))

from fontlib import charsets, registry, session as fsession        # noqa: E402
from fontlib import autotune                                     # noqa: E402
from fontlib import importer                                     # noqa: E402

try:                                                             # noqa: E402
    import theme as _theme
except Exception:                                                # pragma: no cover
    _theme = None


class _ImportDialog:
    def __init__(self, master, on_done=None, initial_paths=None):
        self.master = master
        self.on_done = on_done
        self.paths = [p for p in (initial_paths or []) if p]
        self.probes = []
        self._preview_img = None
        self._busy = False
        self._q = __import__("queue").Queue()

        self.win = tk.Toplevel(master)
        self.win.title("上传字体文件")
        self.win.transient(master)
        try:
            self.win.geometry("660x560")
            self.win.minsize(560, 460)
        except Exception:
            pass

        head = ttk.Frame(self.win, padding=(14, 12, 14, 4))
        head.pack(fill="x")
        ttk.Label(head, text="上传自己的字体文件",
                  font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(
            head, foreground="#6b7280", wraplength=600, justify="left",
            text="支持 TTF / OTF / TTC（字嗨等 App 导出的手写字体、从别的机器"
                 "拷来的字库都行）。导入后会自动判断是汉字库还是符号库、"
                 "算好字号与字距校准，并登记到字库列表。\n"
                 "请只导入你自己有权使用的字体。").pack(anchor="w", pady=(4, 0))

        tools = ttk.Frame(self.win, padding=(14, 6))
        tools.pack(fill="x")
        ttk.Button(tools, text="选择字体文件…", command=self._add).pack(side="left")
        ttk.Button(tools, text="移除选中", command=self._remove).pack(
            side="left", padx=(6, 0))
        self.lbl_count = ttk.Label(tools, text="尚未选择文件",
                                   foreground="#6b7280")
        self.lbl_count.pack(side="left", padx=10)

        mid = ttk.Frame(self.win, padding=(14, 0))
        mid.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(mid, height=5, exportselection=False)
        self.listbox.pack(fill="x")
        self.info = tk.Text(mid, height=7, wrap="word", bd=0,
                            background="#f6f8fa", foreground="#374151")
        self.info.pack(fill="x", pady=(6, 0))
        self.info.configure(state="disabled")

        form = ttk.Frame(self.win, padding=(14, 8))
        form.pack(fill="x")
        ttk.Label(form, text="字库名称").grid(row=0, column=0, sticky="w")
        self.var_name = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self.var_name, width=26).grid(
            row=0, column=1, sticky="w", padx=(6, 16))
        ttk.Label(form, text="字库 id").grid(row=0, column=2, sticky="w")
        self.var_id = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self.var_id, width=18).grid(
            row=0, column=3, sticky="w", padx=6)
        self.var_activate = tk.BooleanVar(value=True)
        ttk.Checkbutton(form, text="导入后设为当前字库",
                        variable=self.var_activate).grid(
            row=1, column=0, columnspan=4, sticky="w", pady=(8, 0))

        self.preview_lbl = ttk.Label(self.win, text="", anchor="center")
        self.preview_lbl.pack(fill="both", expand=True, padx=14, pady=(4, 0))

        bottom = ttk.Frame(self.win, padding=(14, 8, 14, 12))
        bottom.pack(fill="x", side="bottom")
        self.status = ttk.Label(bottom, text="", foreground="#6b7280")
        self.status.pack(side="left")
        ttk.Button(bottom, text="关闭", command=self.close).pack(side="right")
        self.btn_import = ttk.Button(bottom, text="导入", command=self._import,
                                     style="Accent.TButton")
        self.btn_import.pack(side="right", padx=(0, 6))

        if self.paths:
            self._probe()
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    # ---- 选文件
    def _add(self):
        ps = filedialog.askopenfilenames(
            parent=self.win,
            title="选择字体文件（多个文件 = 同一字库的多遍，按主→副顺序）",
            filetypes=[("字体文件", "*.ttf *.otf *.ttc *.otc"),
                       ("全部", "*.*")])
        if not ps:
            return
        for p in ps:
            if p not in self.paths:
                self.paths.append(p)
        self._probe()

    def _remove(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            if 0 <= i < len(self.paths):
                self.paths.pop(i)
        self._probe()

    def _probe(self):
        self.listbox.delete(0, "end")
        for p in self.paths:
            self.listbox.insert("end", "  " + os.path.basename(p))
        self.probes = importer.probe_all(self.paths) if self.paths else []
        self.lbl_count.configure(text="已选 %d 个文件" % len(self.paths)
                                 if self.paths else "尚未选择文件")
        self.info.configure(state="normal")
        self.info.delete("1.0", "end")
        ok = [p for p in self.probes if p["ok"]]
        for p in self.probes:
            if p["ok"]:
                self.info.insert("end", "✔ %s\n    字体名：%s\n    字符 %d"
                                        "（汉字 %d / ASCII %d）  字形 %d  upem %d\n"
                                        "    判断：%s\n" % (
                                            os.path.basename(p["path"]),
                                            p["name"], p["chars"], p["han"],
                                            p["ascii"], p["glyphs"], p["upem"],
                                            "汉字库" if p["kind"] == "han"
                                            else "符号库"))
            else:
                self.info.insert("end", "✘ %s：%s\n" % (
                    os.path.basename(p.get("path", "")), p.get("error")))
        self.info.configure(state="disabled")
        if ok and not self.var_name.get():
            self.var_name.set(ok[0]["name"])
            self.var_id.set(importer.slug(ok[0]["name"]))
        self.btn_import.configure(state="normal" if ok else "disabled")

    # ---- 导入
    def _import(self):
        if self._busy or not self.paths:
            return
        self._busy = True
        self.btn_import.configure(state="disabled")
        self.status.configure(text="正在导入并校准…")
        self.win.config(cursor="watch")
        name = self.var_name.get().strip() or None
        lib_id = self.var_id.get().strip() or None
        act = self.var_activate.get()

        def work():
            try:
                res = importer.import_fonts(self.paths, lib_id=lib_id,
                                            name=name, activate=act)
                self._q.put((res, None))
            except Exception as e:      # noqa: BLE001
                self._q.put((None, e))

        threading.Thread(target=work, daemon=True).start()
        self.win.after(150, self._poll)

    def _poll(self):
        import queue as _q
        try:
            res, err = self._q.get_nowait()
        except _q.Empty:
            self.win.after(150, self._poll)
            return
        self._busy = False
        self.win.config(cursor="")
        if err is not None:
            self.status.configure(text="导入失败")
            self.btn_import.configure(state="normal")
            messagebox.showerror("上传字体文件", str(err), parent=self.win)
            return
        self.status.configure(
            text="已导入「%s」：%d 字，%s%s" % (
                res["name"], res["char_count"],
                "汉字库" if res["kind"] == "han" else "符号库",
                "（已设为当前）" if self.var_activate.get() else ""))
        self.btn_import.configure(text="已导入", state="disabled")
        self._show_preview(res.get("preview"))
        try:
            if self.on_done:
                self.on_done()
        except Exception:
            pass

    def _show_preview(self, path):
        if not path or not os.path.exists(path):
            return
        from PIL import Image, ImageTk
        try:
            im = Image.open(path)
        except Exception:
            return
        maxw = max(320, self.win.winfo_width() - 60)
        if im.width > maxw:
            im = im.resize((maxw, int(im.height * maxw / im.width)),
                           Image.LANCZOS)
        if im.height > 220:
            im = im.resize((int(im.width * 220 / im.height), 220),
                           Image.LANCZOS)
        self._preview_img = ImageTk.PhotoImage(im)
        self.preview_lbl.configure(image=self._preview_img,
                                   text="导入后的字形总览")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


def open_window(master, on_close=None, prefill_text=""):
    """打开工作台（单例：已经开着就抬到前面）。

    ``prefill_text`` 会填进「自定义」字表，主界面「补缺字…」按钮用它
    把当前正文里缺的字直接带过来。
    """
    cur = getattr(master, "_fontlab_win", None)
    if cur is not None:
        try:
            cur.win.lift()
            cur.win.focus_force()
            if prefill_text:
                cur.var_preset.set("自定义（粘贴正文 / 选文件）")
                cur.var_custom.set(prefill_text)
                cur.var_exclude.set(False)
                cur.nb.select(cur.tab_new)
            return cur
        except Exception:
            pass
    win = FontLabWindow(master, on_close=on_close, prefill_text=prefill_text)
    try:
        master._fontlab_win = win
    except Exception:
        pass
    return win


class FontLabWindow:
    def __init__(self, master, on_close=None, prefill_text=""):
        self.master = master
        self.on_close = on_close
        self.session = fsession.Session.open_existing()
        self._preview_img = None
        self._glyph_img = None
        self._busy = False
        self._build_ui()
        if not hasattr(self, "_beach"):
            self._beach = None
        self.refresh_libs()
        if self.session:
            self._bind_session(self.session)
        if prefill_text:
            self.var_preset.set("自定义（粘贴正文 / 选文件）")
            self.var_custom.set(prefill_text)
            self.var_exclude.set(False)
            self._count_chars()
        self.win.protocol("WM_DELETE_WINDOW", self.close)

    # ------------------------------------------------------------ 界面
    def _build_ui(self):
        self.win = tk.Toplevel(self.master)
        if _theme is not None:
            try:
                _theme.apply_theme(self.win, dark=False)
            except Exception:
                pass
        self.win.title("字库工作台 — 自建手写库")
        try:
            sw = self.win.winfo_screenwidth()
            sh = self.win.winfo_screenheight()
            w = min(1220, max(980, sw - 120))
            h = min(840, max(660, sh - 140))
        except Exception:
            w, h = 1180, 800
        self.win.geometry("%dx%d" % (w, h))
        self.win.minsize(980, 660)

        head = ttk.Frame(self.win, padding=(12, 8))
        head.pack(fill="x")
        try:
            dpi = max(1.0, float(self.win.winfo_fpixels("1i")) / 96.0)
        except Exception:
            dpi = 1.0
        if _theme is not None and getattr(_theme, "BeachStrip", None):
            try:
                self._beach = _theme.BeachStrip(
                    self.win, palette=dict(_theme.PALETTE), dpi=dpi,
                    height=72)
                self._beach.pack(fill="x")
            except Exception:
                self._beach = None
        ttk.Label(head, text="字库工作台",
                  font=("Microsoft YaHei UI", 14, "bold")).pack(side="left")
        ttk.Label(head, text="选字 → 打印模板 → 手写 → 上传 → 自动识别 → 微调 → 建库",
                  foreground="#6b7280").pack(side="left", padx=(10, 0))
        ttk.Button(head, text="刷新", command=self.refresh_libs).pack(side="right")

        body = ttk.Panedwindow(self.win, orient="horizontal")
        body.pack(fill="both", expand=True, padx=10, pady=(0, 6))

        left = ttk.Frame(body, width=330)
        left.pack_propagate(False)
        body.add(left, weight=0)
        ttk.Label(left, text="我的字库", font=("Microsoft YaHei UI", 10, "bold")
                  ).pack(anchor="w", pady=(2, 4))
        cols = ("name", "count", "state")
        self.tree = ttk.Treeview(left, columns=cols, show="headings", height=14)
        for c, t, w in (("name", "名称", 170), ("count", "字数", 60),
                        ("state", "状态", 80)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "name" else "center")
        self.tree.pack(fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._show_lib())

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(6, 0))
        ttk.Button(btns, text="设为当前", command=self._activate_lib
                   ).grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=1)
        ttk.Button(btns, text="停用/启用", command=self._toggle_lib
                   ).grid(row=0, column=1, sticky="ew", padx=(3, 0), pady=1)
        ttk.Button(btns, text="重新校准", command=self._recalibrate_lib
                   ).grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=1)
        ttk.Button(btns, text="删除…", command=self._delete_lib
                   ).grid(row=1, column=1, sticky="ew", padx=(3, 0), pady=1)
        btns.columnconfigure(0, weight=1)
        btns.columnconfigure(1, weight=1)

        self.lib_info = tk.Text(left, height=8, wrap="word", bd=0,
                                background="#f6f8fa", foreground="#374151")
        self.lib_info.pack(fill="x", pady=(6, 0))
        self.lib_info.configure(state="disabled")

        right = ttk.Notebook(body)
        body.add(right, weight=3)
        self.nb = right
        self.tab_new = ttk.Frame(right, padding=10)
        self.tab_scan = ttk.Frame(right, padding=10)
        self.tab_preview = ttk.Frame(right, padding=10)
        right.add(self.tab_new, text="① 新建 / 补字")
        right.add(self.tab_scan, text="② 上传与识别")
        right.add(self.tab_preview, text="③ 微调 · 预览 · 生成")

        self._build_new_tab(self.tab_new)
        self._build_scan_tab(self.tab_scan)
        self._build_preview_tab(self.tab_preview)

        self.status = ttk.Label(self.win, text="就绪", padding=(12, 4),
                                foreground="#6b7280")
        self.status.pack(fill="x", side="bottom")

    # ---- ① 新建
    def _build_new_tab(self, p):
        # 已有字体文件的人不用走「选字→模板→扫描」，直接上传
        imp = ttk.LabelFrame(p, text="已有字体文件（TTF / OTF / TTC）？", padding=8)
        imp.pack(fill="x", pady=(0, 10))
        ttk.Label(imp, foreground="#6b7280", wraplength=690, justify="left",
                  text="字嗨等 App 导出的手写字体、从旧电脑拷来的字库，"
                       "可以直接上传：自动识别汉字库/符号库、自动校准字号与字距。"
                  ).pack(anchor="w")
        ttk.Button(imp, text="上传字体文件…", command=self._import_font,
                   style="TButton").pack(anchor="w", pady=(6, 0))

        row = ttk.Frame(p)
        row.pack(fill="x")
        ttk.Label(row, text="字库名称").pack(side="left")
        self.var_name = tk.StringVar(value="我的补字库")
        ttk.Entry(row, textvariable=self.var_name, width=24).pack(
            side="left", padx=6)
        ttk.Label(row, text="字库 id").pack(side="left", padx=(8, 0))
        self.var_id = tk.StringVar(value="my-hand")
        ttk.Entry(row, textvariable=self.var_id, width=16).pack(
            side="left", padx=6)

        row2 = ttk.Frame(p)
        row2.pack(fill="x", pady=(10, 0))
        ttk.Label(row2, text="字表来源").pack(side="left")
        self.preset_labels = {
            "补当前字库缺的字（推荐）": "missing",
            "常用汉字 3755（GB2312 一级）": "gb2312-l1",
            "常用 + 次常用 6763": "gb2312",
            "CJK 基本区 20902（含生僻）": "cjk",
            "自定义（粘贴正文 / 选文件）": "custom",
        }
        self.var_preset = tk.StringVar(value="补当前字库缺的字（推荐）")
        cb = ttk.Combobox(row2, textvariable=self.var_preset, state="readonly",
                          values=list(self.preset_labels), width=30)
        cb.pack(side="left", padx=6)
        cb.bind("<<ComboboxSelected>>", lambda e: self._count_chars())

        row3 = ttk.Frame(p)
        row3.pack(fill="x", pady=(8, 0))
        self.var_custom = tk.StringVar(value="")
        ttk.Entry(row3, textvariable=self.var_custom).pack(
            side="left", fill="x", expand=True)
        ttk.Button(row3, text="选文件…", command=self._pick_charset_file
                   ).pack(side="left", padx=4)
        self.charset_file = ""

        row4 = ttk.Frame(p)
        row4.pack(fill="x", pady=(8, 0))
        self.var_exclude = tk.BooleanVar(value=True)
        ttk.Checkbutton(row4, text="排除当前字库已有的字",
                        variable=self.var_exclude,
                        command=self._count_chars).pack(side="left")
        self.var_rare = tk.BooleanVar(value=False)
        ttk.Checkbutton(row4, text="包含生僻字（GB2312 以外）",
                        variable=self.var_rare,
                        command=self._count_chars).pack(side="left", padx=12)

        row5 = ttk.Frame(p)
        row5.pack(fill="x", pady=(10, 0))
        ttk.Label(row5, text="写几遍").pack(side="left")
        self.var_passes = tk.StringVar(value="2")
        ttk.Spinbox(row5, from_=1, to=4, width=4, textvariable=self.var_passes
                    ).pack(side="left", padx=(4, 14))
        ttk.Label(row5, text="格子大小 mm").pack(side="left")
        self.var_cellmm = tk.StringVar(value="11.3")
        ttk.Spinbox(row5, from_=6, to=30, increment=0.5, width=6,
                    textvariable=self.var_cellmm).pack(side="left", padx=4)

        self.lbl_count = ttk.Label(p, text="—", foreground="#374151")
        self.lbl_count.pack(anchor="w", pady=(12, 0))

        ttk.Button(p, text="生成书写模板（PDF）", command=self._make_template
                   ).pack(anchor="w", pady=(10, 4))
        for var in (self.var_cellmm, self.var_passes, self.var_custom):
            var.trace_add("write", lambda *a: self._count_chars())
        ttk.Label(p, foreground="#6b7280", wraplength=680, justify="left",
                  text="模板会按字表自动分页，四角有定位黑块；打印后每格写一个字"
                       "（写满格、居中，浅灰参考字不要描），再拍照/扫描上传。"
                       "数位板可以直接把模板当背景层，导出整页 PNG。"
                  ).pack(anchor="w")
        self._count_chars()

    # ---- ② 识别
    def _build_scan_tab(self, p):
        row = ttk.Frame(p)
        row.pack(fill="x")
        ttk.Button(row, text="选择扫描图 / PDF…", command=self._pick_images
                   ).pack(side="left")
        ttk.Button(row, text="开始自动识别", command=self._analyze
                   ).pack(side="left", padx=6)
        ttk.Button(row, text="恢复上次工程", command=self._open_latest
                   ).pack(side="left")
        self.lbl_scan = ttk.Label(row, text="未选择文件", foreground="#6b7280")
        self.lbl_scan.pack(side="left", padx=10)

        row2 = ttk.Frame(p)
        row2.pack(fill="x", pady=(8, 0))
        ttk.Label(row2, text="墨迹阈值").pack(side="left")
        self.var_threshold = tk.StringVar(value="auto")
        ttk.Entry(row2, textvariable=self.var_threshold, width=8).pack(
            side="left", padx=4)
        ttk.Label(row2, text="（auto=自动；偏淡就调大）",
                  foreground="#9ca3af").pack(side="left")
        self.var_align = tk.BooleanVar(value=True)
        ttk.Checkbutton(row2, text="四角对齐", variable=self.var_align
                        ).pack(side="left", padx=12)

        self.scan_log = tk.Text(p, height=7, wrap="word", bd=0,
                                background="#f6f8fa", foreground="#374151")
        self.scan_log.pack(fill="x", pady=(8, 0))

        params = ttk.LabelFrame(p, text="整库参数（自动建议，可微调）", padding=8)
        params.pack(fill="x", pady=(8, 0))
        self.var_scale = tk.StringVar(value="1.000")
        self.var_dx = tk.StringVar(value="0")
        self.var_dy = tk.StringVar(value="0")
        for col, (lab, var) in enumerate((("缩放", self.var_scale),
                                          ("水平位移", self.var_dx),
                                          ("垂直位移", self.var_dy))):
            ttk.Label(params, text=lab).grid(row=0, column=col * 2, sticky="w",
                                             padx=(0, 4))
            ttk.Entry(params, textvariable=var, width=8).grid(
                row=0, column=col * 2 + 1, padx=(0, 16))
        ttk.Button(params, text="重新自动建议",
                   command=self._suggest).grid(row=0, column=6)
        ttk.Label(params, foreground="#9ca3af",
                  text="位移单位是 font unit（1000 = 一个字高）；微调后点「预览」看效果。"
                  ).grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

        tune = ttk.LabelFrame(p, text="逐字微调（可选）", padding=8)
        tune.pack(fill="both", expand=True, pady=(8, 0))
        left = ttk.Frame(tune)
        left.pack(side="left", fill="y")
        self.char_list = tk.Listbox(left, width=10, height=8, exportselection=False)
        self.char_list.pack(side="left", fill="y")
        self.char_list.bind("<<ListboxSelect>>", lambda e: self._show_glyph())
        self.glyph_lbl = ttk.Label(left, text="")
        self.glyph_lbl.pack(side="left", padx=8, anchor="n")

        right = ttk.Frame(tune)
        right.pack(side="left", padx=12)
        self.var_gscale = tk.StringVar(value="1.00")
        self.var_gdx = tk.StringVar(value="0")
        self.var_gdy = tk.StringVar(value="0")
        for i, (lab, var) in enumerate((("缩放", self.var_gscale),
                                        ("dx", self.var_gdx),
                                        ("dy", self.var_gdy))):
            ttk.Label(right, text=lab).grid(row=i, column=0, sticky="w")
            ttk.Entry(right, textvariable=var, width=8).grid(
                row=i, column=1, pady=2)
        ttk.Button(right, text="应用这个字", command=self._apply_tweak
                   ).grid(row=3, column=0, columnspan=2, pady=(6, 0), sticky="w")
        ttk.Label(right, foreground="#9ca3af", wraplength=220, justify="left",
                  text="只修正个别写歪/偏大偏小的字；批量问题改上面的整库参数。"
                  ).grid(row=4, column=0, columnspan=2, pady=(6, 0), sticky="w")

    # ---- ③ 预览与生成
    def _build_preview_tab(self, p):
        row = ttk.Frame(p)
        row.pack(fill="x")
        ttk.Label(row, text="样文").pack(side="left")
        self.var_sample = tk.StringVar(
            value="三相电路中，相电压等于线电压除以根号三。电阻R=10Ω，"
                  "电流I=220/5=44A，功率因数cosφ≈0.999。")
        ttk.Entry(row, textvariable=self.var_sample).pack(
            side="left", fill="x", expand=True, padx=6)
        ttk.Button(row, text="预览效果", command=self._preview
                   ).pack(side="left")

        opts = ttk.Frame(p)
        opts.pack(fill="x", pady=(8, 0))
        self.var_merge = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="并入当前字库（补字：老库字形 + 新写字形 = 一套）",
                        variable=self.var_merge).pack(side="left")
        self.var_activate = tk.BooleanVar(value=True)
        ttk.Checkbutton(opts, text="生成后设为当前字库",
                        variable=self.var_activate).pack(side="left", padx=12)
        ttk.Button(opts, text="生成正式字库", command=self._build_final
                   ).pack(side="right")

        self.preview_lbl = ttk.Label(p, text="（点「预览效果」看 1:1 样文对比）",
                                     anchor="nw", justify="left")
        self.preview_lbl.pack(fill="both", expand=True, pady=(10, 0))

        self.build_log = tk.Text(p, height=6, wrap="word", bd=0,
                                 background="#f6f8fa", foreground="#374151")
        self.build_log.pack(fill="x", pady=(8, 0))

    # ------------------------------------------------------------ 异步
    def _bg(self, fn, done):
        """后台跑 fn，结果经队列回主线程。

        不直接在 worker 里调 ``after``：tkinter 不是线程安全的，没进 mainloop
        时会抛「main thread is not in main loop」，进了 mainloop 也有竞态。
        统一用 Queue + 主线程轮询。
        """
        if self._busy:
            return
        self._busy = True
        self.status.configure(text="处理中…")
        self.win.config(cursor="watch")
        if not hasattr(self, "_q"):
            import queue as _queue
            self._q = _queue.Queue()

        def worker():
            try:
                res, err = fn(), None
            except Exception as e:      # noqa: BLE001
                res, err = None, e
            self._q.put((res, err, done))

        threading.Thread(target=worker, daemon=True).start()
        self.win.after(120, self._poll_queue)

    def _poll_queue(self):
        import queue as _queue
        try:
            res, err, done = self._q.get_nowait()
        except _queue.Empty:
            self.win.after(120, self._poll_queue)
            return
        self._done(res, err, done)

    def _done(self, res, err, done):
        self._busy = False
        self.win.config(cursor="")
        self.status.configure(text="就绪" if not err else "出错：%s" % err)
        if err is None:
            try:
                done(res)
            except Exception as e:      # noqa: BLE001
                self._err(e)
        else:
            self._err(err)

    def _err(self, e):
        import traceback
        traceback.print_exc()
        messagebox.showerror("字库工作台", str(e), parent=self.win)

    def _say(self, msg, widget=None):
        w = widget or self.scan_log
        try:
            w.insert("end", msg + "\n")
            w.see("end")
        except Exception:
            pass

    # ------------------------------------------------------------ 字库列表
    def refresh_libs(self):
        data = registry.ensure()
        for iid in self.tree.get_children():
            self.tree.delete(iid)
        active = (data.get("active") or {}).get("han")
        for lib in data["libraries"]:
            state = "缺文件" if lib.get("missing") else (
                "当前" if lib["id"] == active else
                ("已停用" if not lib["enabled"] else "可用"))
            self.tree.insert("", "end", iid=lib["id"],
                             values=(lib.get("name") or lib["id"],
                                     lib.get("char_count") or 0, state))
        try:
            self.win.update_idletasks()
        except Exception:
            pass

    def _sel_lib(self):
        sel = self.tree.selection()
        return sel[0] if sel else ""

    def _show_lib(self):
        lid = self._sel_lib()
        lib = registry.get(lid) if lid else None
        self.lib_info.configure(state="normal")
        self.lib_info.delete("1.0", "end")
        if lib:
            d = registry.dir_of(lib)
            text = ("%s\nid: %s\n类型: %s\n字数: %s\n遍数: %s\n目录: %s\n"
                    "许可: %s\n说明: %s" % (
                        lib.get("name"), lib["id"], lib.get("kind"),
                        lib.get("char_count"), len(lib.get("passes") or []),
                        d, lib.get("license"), lib.get("note") or "—"))
            self.lib_info.insert("1.0", text)
        self.lib_info.configure(state="disabled")

    def _activate_lib(self):
        lid = self._sel_lib()
        if not lid:
            return
        try:
            registry.set_active("han", lid)
            self.refresh_libs()
            self._notify()
            self.status.configure(text="已设为当前字库：%s" % lid)
        except Exception as e:      # noqa: BLE001
            self._err(e)

    def _toggle_lib(self):
        lid = self._sel_lib()
        lib = registry.get(lid) if lid else None
        if not lib:
            return
        registry.set_enabled(lid, not lib["enabled"])
        self.refresh_libs()
        self._notify()

    def _delete_lib(self):
        lid = self._sel_lib()
        lib = registry.get(lid) if lid else None
        if not lib:
            return
        if lib.get("builtin"):
            messagebox.showinfo("字库工作台", "内置字库不能删除。", parent=self.win)
            return
        if not messagebox.askyesno(
                "删除字库",
                "确定删除「%s」？\n目录也会一起删除，不能恢复。" % lib.get("name"),
                parent=self.win):
            return
        try:
            registry.unregister(lid, delete_files=True)
            self.refresh_libs()
            self._notify()
            self.status.configure(text="已删除：%s" % lid)
        except Exception as e:      # noqa: BLE001
            self._err(e)

    # ------------------------------------------------------------ 上传字体
    def _import_font(self):
        """直接上传现成字体文件（不写模板、不扫描）。"""
        return import_dialog(self.win, on_done=self._after_import)

    def _after_import(self):
        self.refresh_libs()
        self._notify()
        self.status.configure(text="字体已导入，可点左侧「设为当前」切换")

    def _recalibrate_lib(self):
        lid = self._sel_lib()
        lib = registry.get(lid) if lid else None
        if not lib:
            return
        paths = registry.pass_paths(lib)

        def work():
            cal = autotune.calibration_for(paths)
            if cal:
                autotune.write_calibration(cal)
            return cal

        self._bg(work, lambda cal: self.status.configure(
            text="已重新校准 %d 套" % len(cal)))
        self._notify()

    def _notify(self):
        try:
            if self.on_close:
                self.on_close()
        except Exception:
            pass

    # ------------------------------------------------------------ ① 新建
    def _preset_id(self):
        return self.preset_labels.get(self.var_preset.get(), "custom")

    def _current_fonts(self):
        lib = registry.active("han")
        return registry.pass_paths(lib) if lib else []

    def _count_chars(self):
        try:
            chars = charsets.resolve(
                self._preset_id(), text=self.var_custom.get(),
                path=self.charset_file,
                font_paths=self._current_fonts(),
                exclude_existing=self.var_exclude.get(),
                drop_rare=not self.var_rare.get())
            d = charsets.describe(chars)
            try:
                from fontlib import template as tpl
                est = tpl.estimate(chars,
                                   cell_pt=tpl.cell_pt_from_mm(
                                       float(self.var_cellmm.get() or 11.3)),
                                   passes=int(self.var_passes.get() or 2))
                pages = "，约 %d 页 A4 × %d 遍" % (est["pages_per_pass"],
                                                 est["passes"])
            except Exception:
                pages = ""
            self.lbl_count.configure(
                text="共 %d 字（汉字 %d，生僻 %d）%s" % (
                    d["总计"], d["汉字"], d["生僻字"], pages))
        except Exception as e:      # noqa: BLE001
            self.lbl_count.configure(text="统计失败：%s" % e)

    def _pick_charset_file(self):
        p = filedialog.askopenfilename(
            parent=self.win,
            filetypes=[("字表/文本", "*.txt *.csv *.json *.xlsx"),
                       ("全部", "*.*")])
        if p:
            self.charset_file = p
            self.var_preset.set("自定义（粘贴正文 / 选文件）")
            self._count_chars()

    def _make_template(self):
        try:
            chars = charsets.resolve(
                self._preset_id(), text=self.var_custom.get(),
                path=self.charset_file,
                font_paths=self._current_fonts(),
                exclude_existing=self.var_exclude.get(),
                drop_rare=not self.var_rare.get())
            if not chars:
                messagebox.showinfo("字库工作台", "这个字表是空的，换个来源试试。",
                                    parent=self.win)
                return
            lib_id = (self.var_id.get() or "my-hand").strip()
            name = self.var_name.get() or lib_id
            passes = int(self.var_passes.get() or 2)
            cell = float(self.var_cellmm.get() or 11.3)
            work = os.path.join(fsession.DEFAULT_ROOT, lib_id)
            os.makedirs(work, exist_ok=True)
            s = fsession.Session.create(work, lib_id, name, chars,
                                        passes=passes, cell_pt=cell)
            self._bind_session(s)
            files = s.make_template()
            self.status.configure(text="模板已生成：%s" % files["pdf"])
            if messagebox.askyesno(
                    "模板已生成",
                    "已生成 %d 页 PDF：\n%s\n\n现在打开看看？" % (
                        len(chars), files["pdf"]), parent=self.win):
                try:
                    os.startfile(files["pdf"])       # noqa: S606
                except Exception:
                    pass
        except Exception as e:      # noqa: BLE001
            self._err(e)

    # ------------------------------------------------------------ ② 识别
    def _pick_images(self):
        ps = filedialog.askopenfilenames(
            parent=self.win,
            filetypes=[("扫描图/PDF", "*.jpg *.jpeg *.png *.bmp *.tif *.tiff *.pdf"),
                       ("全部", "*.*")])
        if not ps:
            return
        if not self.session:
            messagebox.showinfo("字库工作台", "先在「① 新建 / 补字」里做一个工程。",
                                parent=self.win)
            return
        self.session.add_images(list(ps))
        self.lbl_scan.configure(text="已选 %d 个文件" % len(ps))
        self._say("已登记 %d 个文件：" % len(ps))
        for p in ps:
            self._say("  " + os.path.basename(p))

    def _analyze(self):
        if not self.session:
            messagebox.showinfo("字库工作台", "先建工程。", parent=self.win)
            return
        s = self.session
        th = self.var_threshold.get() or "auto"
        if th != "auto":
            try:
                th = int(th)
            except ValueError:
                th = "auto"
        align = self.var_align.get()

        def work():
            rep = s.analyze(threshold=th, auto_align=align)
            sug = s.suggest()
            return rep, sug

        def done(res):
            rep, sug = res
            self._say("识别完成：%d 格，空白 %d，贴边 %d" % (
                rep["cells"], rep["blank"], rep["edge"]), self.scan_log)
            for w in rep["warnings"][:5]:
                self._say("  · " + w, self.scan_log)
            self.var_scale.set("%.4f" % sug["params"]["scale"])
            self.var_dx.set(str(sug["params"]["dx"]))
            self.var_dy.set(str(sug["params"]["dy"]))
            self.char_list.delete(0, "end")
            for ch in s.chars:
                self.char_list.insert("end", ch)
            bad = len(sug["warnings"])
            if bad:
                self._say("需要复核 %d 个字（左侧列表里点字看字形）。" % bad,
                          self.scan_log)
                for ch, why in list(sug["warnings"].items())[:12]:
                    self._say("  %s：%s" % (ch, "；".join(why)), self.scan_log)
            self.nb.select(self.tab_preview)

        self._bg(work, done)

    def _suggest(self):
        if not self.session:
            return
        try:
            sug = self.session.suggest()
            self.var_scale.set("%.4f" % sug["params"]["scale"])
            self.var_dx.set(str(sug["params"]["dx"]))
            self.var_dy.set(str(sug["params"]["dy"]))
            self.status.configure(text="已按中位字高重新建议参数")
        except Exception as e:      # noqa: BLE001
            self._err(e)

    def _show_glyph(self):
        if not self.session:
            return
        sel = self.char_list.curselection()
        if not sel:
            return
        ch = self.char_list.get(sel[0])
        cells = [c for c in self.session.cells() if c[0] == ch]
        if not cells:
            return
        from PIL import Image, ImageTk
        mask = cells[0][1]
        im = Image.fromarray((mask * 255).astype("uint8"), mode="L")
        im = im.resize((120, 120), Image.Resampling.NEAREST)
        self._glyph_img = ImageTk.PhotoImage(im)
        self.glyph_lbl.configure(image=self._glyph_img, text="")
        tw = (self.session.data.get("tweaks") or {}).get(ch) or {}
        self.var_gscale.set(str(tw.get("scale", 1.0)))
        self.var_gdx.set(str(tw.get("dx", 0)))
        self.var_gdy.set(str(tw.get("dy", 0)))

    def _apply_tweak(self):
        sel = self.char_list.curselection()
        if not sel or not self.session:
            return
        ch = self.char_list.get(sel[0])
        try:
            self.session.tweak(ch,
                               scale=float(self.var_gscale.get() or 1),
                               dx=float(self.var_gdx.get() or 0),
                               dy=float(self.var_gdy.get() or 0))
            self.status.configure(text="已记录 %s 的微调（重新生成后生效）" % ch)
        except Exception as e:      # noqa: BLE001
            self._err(e)

    # ------------------------------------------------------------ ③ 预览/生成
    def _bind_session(self, s):
        self.session = s
        self.var_name.set(s.name)
        self.var_id.set(s.id)
        self.var_passes.set(str(s.passes))
        if s.data.get("cell_pt"):
            self.var_cellmm.set("%.1f" % (float(s.data["cell_pt"]) / (72 / 25.4)))
        self.lbl_scan.configure(
            text="工程 %s（%d 字）" % (s.id, len(s.chars)))
        self.nb.select(self.tab_scan)

    def _open_latest(self):
        s = fsession.Session.open_existing()
        if not s:
            messagebox.showinfo("字库工作台", "还没有历史工程。", parent=self.win)
            return
        self._bind_session(s)

    def _gather_params(self):
        try:
            params = {
                "scale": float(self.var_scale.get() or 1.0),
                "dx": float(self.var_dx.get() or 0),
                "dy": float(self.var_dy.get() or 0),
                "baseline": 100,
            }
        except ValueError:
            params = {}
        return params

    def _preview(self):
        if not self.session:
            messagebox.showinfo("字库工作台", "先做工程并识别。", parent=self.win)
            return
        s = self.session
        params = self._gather_params()
        merge = self.var_merge.get()
        text = self.var_sample.get()

        def work():
            s.set_params(params)
            meta = s.build(register=False, write_calibration=False,
                           merge_from=(registry.active("han") or {}).get("id")
                           if merge else None)
            out = s.preview(text=text, cal=meta.get("calibration"))
            return out, meta

        def done(res):
            out, meta = res
            self._show_preview_image(out)
            self._say("预览已生成（%d 字，%d 遍）" % (
                meta["char_count"], len(meta["passes_abs"])), self.build_log)

        self._bg(work, done)

    def _show_preview_image(self, path):
        from PIL import Image, ImageTk
        try:
            im = Image.open(path)
        except Exception:
            return
        maxw = max(400, self.preview_lbl.winfo_width() - 10)
        if im.width > maxw:
            im = im.resize((maxw, int(im.height * maxw / im.width)),
                           Image.LANCZOS)
        self._preview_img = ImageTk.PhotoImage(im)
        self.preview_lbl.configure(image=self._preview_img, text="")

    def _build_final(self):
        if not self.session:
            return
        s = self.session
        params = self._gather_params()
        merge = (registry.active("han") or {}).get("id") if self.var_merge.get() else None
        activate = self.var_activate.get()

        def work():
            s.set_params(params)
            return s.build(merge_from=merge, activate=activate)

        def done(meta):
            self.refresh_libs()
            self._notify()
            self._say("字库已生成：%s" % meta["target_dir"], self.build_log)
            self._say("字数 %d，%d 遍；校准 size_scale=%s" % (
                meta["char_count"], len(meta["passes_abs"]),
                ", ".join("%.3f" % v["size_scale"]
                          for v in meta["calibration"].values())),
                self.build_log)
            if meta.get("preview"):
                self._say("字形总览：%s" % meta["preview"], self.build_log)
            try:
                self._show_preview_image(
                    s.preview(cal=meta.get("calibration")))
            except Exception:
                pass
            messagebox.showinfo(
                "生成完成",
                "字库已生成：%s\n%s" % (
                    meta["target_dir"],
                    "已设为当前字库，回到主界面渲染即可看到。"
                    if activate else "未启用，左侧列表里点「设为当前」即可。"),
                parent=self.win)

        self._bg(work, done)

    # ------------------------------------------------------------ 关闭
    def close(self):
        try:
            if getattr(self, "_beach", None) is not None:
                self._beach.stop()
        except Exception:
            pass
        try:
            self.master._fontlab_win = None
        except Exception:
            pass
        try:
            self.win.destroy()
        except Exception:
            pass
        self._notify()


if __name__ == "__main__":
    try:
        import gui
        gui.enable_dpi_awareness()
    except Exception:
        pass
    root = tk.Tk()
    root.withdraw()
    try:
        from theme import apply_theme
        apply_theme(root)
    except Exception:
        pass
    w = FontLabWindow(root)
    root.mainloop()
