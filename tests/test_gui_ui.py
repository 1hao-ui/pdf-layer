# -*- coding: utf-8 -*-
"""tests/test_gui_ui.py — 桌面界面（M5）的无头回归。

跑法（在 源码/pdf-layer 下）：
    py -3 tests\\test_gui_ui.py

覆盖 2026-11 沙滩主题改版后的结构约定：
  · 沙滩 / 夜海两套调色板、新增的 ttk 语义样式（Brand / Chip / Section）
  · 三页工作台（书写 / 样式 / 输出）切换与导航高亮，画布常驻不重建
  · 日志抽屉默认收起、可开可收，收起后画布高度要还回去
  · 段落列表（单行紧凑模式）与增删
  · 纸面空白处拖框 = 重设当前段区域（不再需要「框选」按钮）
  · 深浅色切换、页面记忆、快捷键绑定

没有 tkinter / 没有显示环境时打印 SKIP 并以 0 退出，不阻塞渲染回归。
"""
import os
import sys
import tempfile
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, REPO)

import theme  # noqa: E402

import gui  # noqa: E402

CFG_DIR = tempfile.mkdtemp(prefix="zti_gui_ui_")
_STATE = {"root": None, "app": None}


def app():
    """惰性创建一个隐藏窗口 + App（全部用例共用）。"""
    if _STATE["app"] is None:
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        _STATE["root"] = root
        _STATE["app"] = gui.App(
            root, auto_render=False,
            config_path=os.path.join(CFG_DIR, "gui_ui.json"))
        root.update()
    return _STATE["app"]


def managed(widget):
    """窗口隐藏时 winfo_ismapped() 恒为 0，用 geometry manager 判断是否被收纳。"""
    return bool(widget.winfo_manager())


class Ev(object):
    def __init__(self, x=0, y=0, delta=0, width=0, height=0):
        self.x, self.y, self.delta = x, y, delta
        self.width, self.height = width, height


# ---------------------------------------------------------------- 主题
def test_theme_palette_beach():
    light = theme.apply_theme(None)["PALETTE"]
    dark = theme.apply_theme(None, dark=True)["PALETTE"]
    assert light["bg"] == "#F6EDDD", light["bg"]
    assert light["canvas"] == "#D5E7E6", light["canvas"]
    assert light["accent"] == "#0C7F8B", light["accent"]
    assert light["danger"] == "#DE6A55", light["danger"]
    # 深色是「夜海」：暖黑沙 + 深海青 + 月光青，不是冷蓝灰
    assert dark["bg"] == "#1D1712", dark["bg"]
    assert dark["canvas"] == "#0B2A30", dark["canvas"]
    assert dark["accent"] == "#5CC9C4", dark["accent"]
    # 纸面永远暖白、墨色永远深（深色模式不反转）
    assert light["paper"] == "#FFFDF8" and light["paper_ink"] == "#27231C"
    assert dark["paper"] == "#FFF8EA" and dark["paper_ink"] == "#241F17"


def test_styles_registered():
    a = app()
    st = gui._ttk.Style(a.root)
    assert st.lookup("Card.TFrame", "background").lower() == "#fffcf4"
    assert st.lookup("Accent.TButton", "background").lower() == "#0c7f8b"
    assert st.lookup("Section.TButton", "background")
    assert st.lookup("Chip.TLabel", "background")
    assert st.lookup("Brand.TLabel", "background")


# ---------------------------------------------------------------- 结构
def test_default_page_is_write():
    import tkinter as tk
    a = app()
    top = tk.Toplevel(a.root)
    top.withdraw()
    b = gui.App(top, auto_render=False,
                config_path=os.path.join(CFG_DIR, "fresh.json"))
    top.update()
    try:
        assert b.page_key == "write", b.page_key
    finally:
        b.close()


def test_pages_switch_and_nav():
    a = app()
    a.show_page("write")
    for key in ("style", "out", "write"):
        a.show_page(key)
        assert a.page_key == key
        assert managed(a._panels[key]), key
        assert a._nav_buttons[key].cget("bg").lower() == "#dbefee"
    assert sum(1 for f in a._panels.values() if managed(f)) == 1


def test_canvas_survives_page_switch():
    a = app()
    canvas = a.canvas
    a.show_page("style")
    a.show_page("out")
    a.show_page("write")
    assert a.canvas is canvas
    assert a.page_image is None or a.canvas.winfo_exists()


def test_console_drawer():
    a = app()
    a.root.update_idletasks()
    assert a._console_open is False
    assert not managed(a._console_wrap)
    h0 = a.paned.winfo_height()
    a.toggle_console()
    a.root.update_idletasks()
    assert a._console_open is True and managed(a._console_wrap)
    a.toggle_console()
    a.root.update_idletasks()
    assert a._console_open is False and not managed(a._console_wrap)
    if h0 > 1:                      # 收起后画布要把高度还回去
        assert a.paned.winfo_height() == h0, (h0, a.paned.winfo_height())


def test_menus_build():
    a = app()
    old = a._popup_menu
    a._popup_menu = lambda menu, widget: None
    try:
        a._show_more_menu()
        a._show_bg_menu()
        a.set_blank_background()
        a._show_bg_menu()
    finally:
        a._popup_menu = old


# ---------------------------------------------------------------- 段落
def test_paragraph_list_and_actions():
    a = app()
    a.page = gui.PM.Page()
    a.page.add_paragraph(gui.PM.Paragraph(text="第一段", rect=[56, 90, 539, 300]))
    a.selected = 0
    a._refresh_paragraph_list()
    assert len(a.para_list._items) == 1
    assert a.para_list._compact is True
    a.add_paragraph(enter_draw=False)
    a.add_paragraph(enter_draw=False)
    a._refresh_paragraph_list()
    assert len(a.page.paragraphs) == 3
    assert len(a.para_list._items) == 3
    a.selected = 2
    a.delete_paragraph()
    assert len(a.page.paragraphs) == 2
    a._refresh_paragraph_list()
    assert a.para_list._compact is True


def test_drag_on_paper_resets_rect():
    a = app()
    a.page = gui.PM.Page()
    a.page.add_paragraph(gui.PM.Paragraph(text="拖框", rect=[56, 120, 300, 200]))
    a.selected = 0
    a._refresh_paragraph_list()
    a._canvas_size_override = (900, 700)
    a.fit_view()
    a.root.update()
    try:
        r0 = list(a.page.paragraphs[0].rect)
        x0, y0 = a._pt2screen(120, 300)
        x1, y1 = a._pt2screen(500, 420)
        a._on_press(Ev(int(x0), int(y0)))
        assert (a._interact or {}).get("kind") == "draw", a._interact
        a._on_motion(Ev(int(x1), int(y1)))
        a._on_release(Ev(int(x1), int(y1)))
        r1 = list(a.page.paragraphs[0].rect)
    finally:
        a._canvas_size_override = None
    assert r1 != r0, r1
    assert a.mode == "select"
    # 区域夹在纸面内
    assert 0 <= r1[0] < r1[2] <= a.page.width_pt
    assert 0 <= r1[1] < r1[3] <= a.page.height_pt


# ---------------------------------------------------------------- 主题切换 / 配置
def test_dark_mode_switch():
    a = app()
    a.set_dark(True)
    a.root.update()
    assert a.dark is True and a.pal["bg"] == "#1D1712", a.pal["bg"]
    assert a.canvas.cget("bg").lower() == "#0b2a30"
    a.set_dark(False)
    a.root.update()
    assert a.dark is False and a.pal["bg"] == "#F6EDDD"
    assert a.canvas.cget("bg").lower() == "#d5e7e6"


def test_page_persisted():
    a = app()
    cfg = os.path.join(CFG_DIR, "restore.json")
    a.show_page("style")
    a.save_config(cfg)
    text = open(cfg, encoding="utf-8").read()
    assert '"page": "style"' in text, text
    import tkinter as tk
    top = tk.Toplevel(a.root)
    top.withdraw()
    b = gui.App(top, auto_render=False, config_path=cfg)
    top.update()
    try:
        assert b.page_key == "style", b.page_key
    finally:
        b.close()


def test_icons_and_beach():
    a = app()
    import icons
    check_names = ("write", "style", "out", "render", "pdf", "plus",
                   "trash", "image", "refresh", "dots")
    for name in check_names:
        assert name in icons.names(), name
    assert icons.image("render", 18, dpi=1.5) is not None
    assert a._icon("render") is not None
    # 沙滩条：主题模块提供；点一下会记录涟漪/脚印
    assert a._beach is not None
    a.root.update_idletasks()
    a._beach.update_idletasks()
    n0 = len(a._beach._marks)
    a._beach._on_click(Ev(40, 60))
    assert len(a._beach._marks) == n0 + 1
    # 海面：纸外点一下才有波纹
    a._canvas_size_override = (900, 700)
    a.fit_view()
    a.root.update()
    try:
        a._sea_ripples = []
        a._add_sea_ripple(2, 2)      # 画布左上角，纸外
        assert len(a._sea_ripples) == 1
        cx, cy = a._pt2screen(a.page.width_pt / 2, a.page.height_pt / 2)
        a._add_sea_ripple(int(cx), int(cy))   # 纸中心：不荡波纹
        assert len(a._sea_ripples) == 1
    finally:
        a._canvas_size_override = None
    a._draw_sea_deco()
    assert a.canvas.find_withtag("seadeco") != ()


def test_shortcuts_bound():
    a = app()
    for seq in ("<Control-Return>", "<F5>", "<Control-d>",
                "<Control-Key-1>", "<Control-Key-2>", "<Control-Key-3>"):
        assert a.root.bind(seq), seq


# ---------------------------------------------------------------- 纸张 / 字体颜色
def test_paper_sizes_and_materials():
    pm = gui.PM
    assert pm.paper_size_pt("A4") == (595.0, 842.0)
    assert pm.paper_size_pt("A4", True) == (842.0, 595.0)
    w, h = pm.paper_size_pt("B5")
    assert abs(w - 176.0 * pm.MM) < 0.01
    assert abs(h - 250.0 * pm.MM) < 0.01
    assert pm.paper_size_label("A4") == "A4  210×297"
    assert set(pm.PAPER_SIZE_ORDER) == set(pm.PAPER_SIZES)
    assert set(pm.PAPER_MATERIAL_ORDER) == set(pm.PAPER_MATERIALS)
    for key in pm.PAPER_MATERIAL_ORDER:
        im = pm.make_paper_image(60, 80, key)
        assert im.size == (60 * pm.SCALE, 80 * pm.SCALE), (key, im.size)
    # 牛皮纸不是白的（材质真的进了像素）
    kraft = pm.make_paper_image(60, 80, "kraft").convert("RGB")
    assert kraft.resize((1, 1)).getpixel((0, 0))[0] < 240


def test_paper_spec_roundtrip():
    pm = gui.PM
    pg = pm.Page()
    pg.set_paper(size="A5", landscape=True, material="ivory")
    pg.add_paragraph(pm.Paragraph(text="纸张", rect=[20, 30, 200, 120]))
    spec = pm.page_to_spec(pg)
    assert spec["paper"] == {"size": "A5", "landscape": True,
                             "material": "ivory"}
    pg2 = pm.page_from_spec(spec)
    assert (pg2.paper_size, pg2.paper_landscape, pg2.paper_kind) == (
        "A5", True, "ivory")
    assert abs(pg2.width_pt - pg.width_pt) < 0.01
    assert abs(pg2.height_pt - pg.height_pt) < 0.01
    # 老项目（没有 paper 字段）按尺寸反查
    old = {"width_pt": 595.0, "height_pt": 842.0, "paragraphs": []}
    pg3 = pm.page_from_spec(old)
    assert pg3.paper_size == "A4" and pg3.paper_kind == "clean"


def test_paper_and_ink_controls():
    a = app()
    p0 = a.page.paragraphs[0]
    try:
        a.set_blank_background()
        a._apply_paper(size="B5")
        assert a.page.paper_size == "B5"
        assert abs(a.page.width_pt - 498.9) < 0.5
        assert p0.right <= a.page.width_pt + 0.01
        a._apply_paper(material="kraft")
        assert a.page.paper_kind == "kraft"
        assert "牛皮" in a._paper_chips["kraft"].cget("text")
        assert "✓" in a._paper_chips["kraft"].cget("text")
        a._apply_paper(landscape=True)
        assert a.page.paper_landscape is True
        assert a.page.width_pt > a.page.height_pt
        a.select_paragraph(0)
        a._set_ink((24, 64, 160))
        assert p0.ink == (24, 64, 160)
        assert a.cfg["ink"] == (24, 64, 160)
        assert "✓" in a._ink_chips[(24, 64, 160)].cget("text")
        a._set_ink((170, 42, 46), apply_all=True)
        assert all(p.ink == (170, 42, 46) for p in a.page.paragraphs)
        # 控制台命令仍可用
        assert a._try_command("paper=A3")
        assert a.page.paper_size == "A3"
        assert a._try_command("material=宣纸")
        assert a.page.paper_kind == "rice"
        assert a._try_command("landscape=on")
        assert a.page.paper_landscape is True
        # 空白页清背景后仍保留纸张偏好
        a.set_blank_background()
        assert a.page.paper_size == "A3" and a.page.paper_landscape is True
        assert a.page.paper_kind == "rice"
    finally:
        a._apply_paper(size="A4", landscape=False, material="clean")
        a._set_ink((16, 16, 18), apply_all=True)
        a.set_blank_background()


def test_paper_and_ink_persisted():
    a = app()
    cfg = os.path.join(CFG_DIR, "paper.json")
    try:
        a._apply_paper(size="A5", landscape=False, material="ivory")
        a._set_ink((92, 64, 40), apply_all=True)
        a.save_config(cfg)
        import tkinter as tk
        top = tk.Toplevel(a.root)
        top.withdraw()
        b = gui.App(top, auto_render=False, config_path=cfg)
        top.update()
        try:
            assert b.page.paper_size == "A5", b.page.paper_size
            assert b.page.paper_kind == "ivory", b.page.paper_kind
            assert b.cfg["ink"] == (92, 64, 40), b.cfg["ink"]
            assert b.page.paragraphs[0].ink == (92, 64, 40)
        finally:
            b.close()
    finally:
        a._apply_paper(size="A4", landscape=False, material="clean")
        a._set_ink((16, 16, 18), apply_all=True)
        a.set_blank_background()


# ---------------------------------------------------------------- 入口
def main():
    if not theme.available():
        print("SKIP 当前环境没有可用的 tkinter/显示，跳过界面回归")
        return 0
    names = [n for n in sorted(globals()) if n.startswith("test_")]
    failed = []
    for name in names:
        try:
            globals()[name]()
        except Exception:
            failed.append(name)
            print("FAIL  " + name)
            traceback.print_exc()
        else:
            print("PASS  " + name)
    # 收尾
    try:
        if _STATE["app"] is not None:
            _STATE["app"].close()
    except Exception:
        pass
    try:
        if _STATE["root"] is not None:
            _STATE["root"].destroy()
    except Exception:
        pass
    total = len(names)
    print("\n%d 项：PASS %d，FAIL %d" % (total, total - len(failed), len(failed)))
    for name in failed:
        print("  FAIL", name)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
