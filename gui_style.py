#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gui_style.py —— WorkBuddy 统一 GUI 界面风格（现代浅色 / ttk-clam 深度定制）

本模块是全机所有带 GUI 的程序的**唯一视觉基准**，抽取自：
    E:\\AI_workspace\\pyaedt\\aedt_gui.py      第 121–323 行（Maxwell 仿真批量后处理）
    E:\\AI_workspace\\pyaedt\\aedt_gui_transient.py（瞬态版，同款）

风格来源注释原文：「现代浅色（风格参考 png2bar_chat，基于 ttk 自带 clam 深度定制）」

设计目标：**单文件、零第三方依赖、可 PyInstaller 打包**。
只依赖 tkinter / ttk 标准库，不引 PySide6 / Qt（PyDracula 路线已于 2026-09-22 弃用）。

--------------------------------------------------------------------------
最短用法（3 行搞定统一风格）
--------------------------------------------------------------------------
    import gui_style as gs

    app = gs.init("我的工具")          # 高DPI + Tk + 主题 + 窗口尺寸/底色，一步到位
    gs.card(app, "参数").pack(...)     # 白色卡片分组框
    app.mainloop()

--------------------------------------------------------------------------
常用片段
--------------------------------------------------------------------------
    # 分栏（所有大内容区都该可拖拽调尺寸）
    pw = gs.paned(app, orient="v")
    pw.add(gs.card(pw, "候选"), minsize=120)
    pw.add(gs.card(pw, "结果"), minsize=120)

    # 按钮三态
    ttk.Button(f, text="执行", style="Accent.TButton")   # 主操作：实心主色白字
    ttk.Button(f, text="刷新")                            # 次操作：白底描边
    ttk.Button(f, text="清空", style="Danger.TButton")    # 危险：白底红字

    # 二级内容一律弹窗，不要内联展开
    win = gs.popup(app, "端口编辑器", w=860, h=620)

    # 非 ttk 控件（ttk 没有 Listbox / Text）
    tk.Listbox(f, **gs.listbox_kw())
    tk.Text(f, **gs.text_kw())

--------------------------------------------------------------------------
风格硬约束（勿自行发挥）
--------------------------------------------------------------------------
1. 基调必须是**浅色**，禁止深色。
2. 主色只允许 #2563EB（hover #1D4ED8，选中底 #E8F0FE）。
3. 字体统一 Microsoft YaHei UI（自动回退 YaHei / Segoe UI）。
4. 所有可装大内容的区域必须用 PanedWindow 做成可拖拽 sash。
5. 二级内容用 Toplevel 弹窗；弹窗尺寸取 POPUP_SIZES 里的档位。
6. 全部输入与样式写 JSON 配置持久化（见 load_json_cfg / save_json_cfg）。
"""

from __future__ import annotations

import json
import os
import sys
import tkinter as tk
from tkinter import ttk

__all__ = [
    "PALETTE", "FONT_FAMILY", "SCALE", "POPUP_SIZES",
    "init", "apply_theme", "setup_style", "enable_hidpi",
    "font", "card", "paned", "popup", "sidebar",
    "listbox_kw", "text_kw",
    "load_json_cfg", "save_json_cfg",
]

# ---------------------------------------------------------------- 调色板
# 与 aedt_gui.py 的 PALETTE 逐字一致，勿改值。
PALETTE = {
    "bg": "#F5F7FA",           # 窗口底色
    "surface": "#FFFFFF",      # 卡片 / 输入框
    "border": "#E1E7EF",       # 边框 / 分隔线
    "text": "#1F2937",         # 主文字
    "muted": "#6B7280",        # 次要文字
    "accent": "#2563EB",       # 主色
    "accent_hover": "#1D4ED8",
    "accent_soft": "#E8F0FE",  # 选中底色
    "danger": "#EF4444",
    "ok": "#16A34A",
    # 左侧竖排菜单
    "menu_bg": "#FBFBFD",      # 菜单底色（比内容区略深一点）
    "menu_fg": "#475569",      # 菜单文字
    "menu_hover": "#F1F5F9",   # 悬停
}

FONT_FAMILY = "Microsoft YaHei UI"   # setup_style() 里会自动探测可用字体
SCALE = 1.0                          # 高 DPI 缩放系数，由 enable_hidpi() 赋值

# 二级弹窗的**标准档位**，新弹窗尺寸从这里挑，别随手写数字
POPUP_SIZES = {
    "small": (720, 320),
    "medium": (860, 620),
    "large": (920, 620),
    "chart": (1080, 790),
    "wide": (1280, 900),
}


# ---------------------------------------------------------------- 高 DPI
def enable_hidpi() -> float:
    """开启 Windows 高 DPI 感知，返回相对 96dpi 的缩放系数。

    必须在创建任何 Tk 窗口**之前**调用，否则不生效。
    """
    global SCALE
    if sys.platform != "win32":
        SCALE = 1.0
        return SCALE
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
        hdc = ctypes.windll.user32.GetDC(0)
        try:
            dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)   # LOGPIXELSX
        finally:
            ctypes.windll.user32.ReleaseDC(0, hdc)
        if dpi:
            SCALE = max(1.0, dpi / 96.0)
    except Exception:
        SCALE = 1.0
    return SCALE


def _set_taskbar_appid(appid: str = "WorkBuddy.GuiStyle.Tool") -> None:
    """把任务栏图标归到本进程（不设的话 Windows 会用 python.exe 的图标）。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(appid)
    except Exception:
        pass


def _pick_font_family(root: tk.Misc) -> str:
    """挑一个既有中文、风格又现代的系统字体。"""
    try:
        from tkinter import font as tkfont
        avail = {f.lower() for f in tkfont.families(root)}
        for name in ("Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"):
            if name.lower() in avail:
                return name
        return tkfont.nametofont("TkDefaultFont").actual()["family"]
    except Exception:
        return "Microsoft YaHei UI"


def font(size: int = 9, bold: bool = False) -> tuple:
    """返回 (family, size[, 'bold']) 元组，供 tk 侧控件使用。"""
    return (FONT_FAMILY, size, "bold") if bold else (FONT_FAMILY, size)


# ---------------------------------------------------------------- 主题
def setup_style(root: tk.Misc) -> None:
    """配置现代浅色主题（基于 clam）。**每个进程只调用一次**，建 Tk() 之后立刻调。"""
    global FONT_FAMILY
    FONT_FAMILY = _pick_font_family(root)
    p = PALETTE
    style = ttk.Style(root)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    # 关掉 Windows Text 控件的拼写检查红波浪线
    try:
        root.tk.createcommand("tk_spellcheck", lambda *args: "")
    except Exception:
        pass

    f_normal = (FONT_FAMILY, 9)
    f_bold = (FONT_FAMILY, 9, "bold")

    style.configure(".", background=p["bg"], foreground=p["text"],
                    font=f_normal, borderwidth=0, troughcolor=p["bg"])
    # 本工具的内容基本都在白卡片/白页里，所以默认底色取 surface
    style.configure("TFrame", background=p["surface"])
    style.configure("Card.TFrame", background=p["surface"])
    style.configure("TSeparator", background=p["border"])

    # 文字
    style.configure("TLabel", background=p["surface"], foreground=p["text"])
    style.configure("Card.TLabel", background=p["surface"], foreground=p["text"])
    style.configure("Muted.TLabel", background=p["surface"],
                    foreground=p["muted"])
    style.configure("Title.TLabel", background=p["surface"],
                    foreground=p["text"], font=(FONT_FAMILY, 13, "bold"))
    style.configure("Status.TLabel", background=p["surface"],
                    foreground=p["muted"])

    # 卡片式分组框
    style.configure("Card.TLabelframe", background=p["surface"],
                    bordercolor=p["border"], borderwidth=1)
    style.configure("Card.TLabelframe.Label", background=p["surface"],
                    foreground=p["muted"], font=f_bold)

    # 标签页
    style.configure("TNotebook", background=p["bg"], borderwidth=0,
                    tabmargins=(2, 8, 2, 0))
    style.configure("TNotebook.Tab", background=p["bg"], foreground=p["muted"],
                    padding=(16, 7), bordercolor=p["border"], font=f_normal)
    style.map("TNotebook.Tab",
              background=[("selected", p["surface"]), ("active", "#EEF2F8")],
              foreground=[("selected", p["accent"])],
              bordercolor=[("selected", p["border"])])

    # 按钮
    style.configure("TButton", background=p["surface"], foreground=p["text"],
                    bordercolor=p["border"], lightcolor=p["surface"],
                    darkcolor=p["surface"], padding=(12, 6))
    style.map("TButton",
              background=[("active", "#F1F5FB"), ("disabled", p["surface"])],
              bordercolor=[("active", "#C7D2E4"), ("focus", p["accent"])],
              foreground=[("disabled", "#9AA3AF")])
    style.configure("Accent.TButton", background=p["accent"],
                    foreground="#FFFFFF", bordercolor=p["accent"],
                    lightcolor=p["accent"], darkcolor=p["accent"],
                    padding=(16, 7), font=f_bold)
    style.map("Accent.TButton",
              background=[("active", p["accent_hover"]), ("disabled", "#A5B4FC")],
              bordercolor=[("active", p["accent_hover"])])
    style.configure("Danger.TButton", background=p["surface"],
                    foreground=p["danger"], bordercolor=p["danger"],
                    lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(14, 6), font=f_bold)
    style.map("Danger.TButton",
              background=[("active", "#FEF2F2"), ("disabled", p["surface"])],
              foreground=[("disabled", "#9AA3AF")])

    # 复选框
    style.configure("TCheckbutton", background=p["surface"],
                    foreground=p["text"], padding=(4, 4),
                    indicatorbackground=p["surface"],
                    indicatorcolor=p["surface"],
                    indicatordiameter=12, bordercolor=p["border"],
                    focuscolor=p["accent_soft"])
    style.map("TCheckbutton",
              background=[("active", p["surface"])],
              indicatorcolor=[("selected", p["accent"]),
                              ("!selected", p["surface"])],
              foreground=[("disabled", "#9AA3AF")])
    style.configure("TRadiobutton", background=p["surface"],
                    foreground=p["text"], padding=(4, 4),
                    indicatorbackground=p["surface"],
                    indicatorcolor=p["surface"],
                    indicatordiameter=12, bordercolor=p["border"])
    style.map("TRadiobutton",
              background=[("active", p["surface"])],
              indicatorcolor=[("selected", p["accent"]),
                              ("!selected", p["surface"])])

    # 输入
    style.configure("TEntry", fieldbackground=p["surface"],
                    foreground=p["text"], bordercolor=p["border"],
                    lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(8, 5))
    style.map("TEntry", bordercolor=[("focus", p["accent"])])
    style.configure("TCombobox", fieldbackground=p["surface"],
                    foreground=p["text"], bordercolor=p["border"],
                    lightcolor=p["surface"], darkcolor=p["surface"],
                    padding=(7, 5), arrowsize=14)
    style.map("TCombobox",
              fieldbackground=[("readonly", p["surface"]),
                               ("disabled", "#F1F3F7")],
              foreground=[("readonly", p["text"])],
              bordercolor=[("focus", p["accent"])],
              arrowcolor=[("disabled", "#9AA3AF")])

    # 滚动条（细、浅色）
    style.configure("TScrollbar", background="#CBD5E1",
                    troughcolor=p["bg"], bordercolor=p["bg"],
                    arrowcolor=p["muted"], arrowsize=13, borderwidth=0)
    style.map("TScrollbar",
              background=[("active", "#B6C2D2"), ("pressed", "#94A3B8")])
    style.configure("Vertical.TScrollbar", width=10)
    style.configure("Horizontal.TScrollbar", width=10)


apply_theme = setup_style   # 别名：两个名字都能用


# ---------------------------------------------------------------- 组合件
def init(title: str = "工具", size: tuple = (1280, 860),
         minsize: tuple = (900, 640), appid: str = None) -> tk.Tk:
    """一行完成：高DPI → 建窗口 → 应用主题 → 设尺寸/底色/标题。

    必须在**任何**其它 tk 控件创建之前调用（高 DPI 感知有顺序要求）。
    """
    _set_taskbar_appid(appid or ("WorkBuddy." + title))
    s = enable_hidpi()
    root = tk.Tk()
    setup_style(root)
    root.title(title)
    root.geometry("%dx%d" % (int(size[0] * s), int(size[1] * s)))
    root.minsize(int(minsize[0] * s), int(minsize[1] * s))
    root.resizable(True, True)
    root.configure(bg=PALETTE["bg"])
    return root


def card(parent: tk.Misc, title: str = None, **kw) -> ttk.Frame:
    """白色卡片分组框；给了 title 就带一个 muted 粗体小标题。"""
    if title is None:
        return ttk.Frame(parent, style="Card.TFrame", **kw)
    lf = ttk.LabelFrame(parent, text=title, style="Card.TLabelframe", **kw)
    return lf


def paned(parent: tk.Misc, orient: str = "h", sash: int = 6) -> tk.PanedWindow:
    """可拖拽分栏。所有大内容区都该用它，而不是固定 grid 比例。"""
    o = tk.VERTICAL if orient.lower().startswith("v") else tk.HORIZONTAL
    return tk.PanedWindow(parent, orient=o, sashwidth=sash,
                          sashrelief="flat", bg=PALETTE["bg"], bd=0)


def popup(root: tk.Misc, title: str, w: int = None, h: int = None,
          size: str = "medium") -> tk.Toplevel:
    """标准二级弹窗。尺寸优先用显式 w/h，否则取 POPUP_SIZES[size]。"""
    if w is None or h is None:
        w, h = POPUP_SIZES.get(size, POPUP_SIZES["medium"])
    px, py = int(w * SCALE), int(h * SCALE)
    win = tk.Toplevel(root)
    win.title(title)
    win.geometry("%dx%d" % (px, py))
    win.configure(bg=PALETTE["bg"])
    win.transient(root)
    # 本次实际请求的像素尺寸。窗口被 withdraw / 尚未映射时 Tk 的
    # wm_geometry() 恒返回 "1x1+0+0"，读不到真值，故在此显式记录供自检/调试。
    win._gs_size = (px, py)
    return win


def sidebar(master, **kw):
    """左侧竖排图标菜单 + 分组页签（**新项目标配**）。

    实现在 `gui_sidebar.py`，此处只是懒加载转发，避免不用的项目也 import 它。
    用法：
        nav = gs.sidebar(app)
        nav.pack(fill="both", expand=True)
        nav.add(ttk.Frame(nav.body), text="总览", icon="home", group="常用")
    """
    import gui_sidebar
    return gui_sidebar.LeftNav(master, **kw)


# ---------------------------------------------------------------- 非 ttk 控件
def listbox_kw() -> dict:
    """tk.Listbox 的现代外观参数（ttk 没有 Listbox）。"""
    p = PALETTE
    return dict(bg=p["surface"], fg=p["text"], font=(FONT_FAMILY, 9),
                relief="flat", borderwidth=0, highlightthickness=1,
                highlightcolor=p["accent"], highlightbackground=p["border"],
                selectbackground=p["accent_soft"],
                selectforeground=p["text"],
                activestyle="none", exportselection=False)


def text_kw() -> dict:
    """tk.Text 的现代外观参数。"""
    p = PALETTE
    return dict(bg=p["surface"], fg=p["text"], font=(FONT_FAMILY, 9),
                relief="flat", borderwidth=0, highlightthickness=1,
                highlightcolor=p["accent"], highlightbackground=p["border"],
                selectbackground=p["accent_soft"],
                selectforeground=p["text"],
                insertwidth=2, padx=6, pady=4, spacing1=1)


# ---------------------------------------------------------------- 配置持久化
def load_json_cfg(path: str, default: dict = None) -> dict:
    """读 JSON 配置；不存在/损坏一律回退 default，绝不抛异常。

    用途：把界面全部输入与样式存盘（对应 web 端 localStorage 的角色），
    保证重开程序不丢状态 —— 这是本风格的硬要求之一。
    """
    base = dict(default or {})
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict):
            base.update(d)
    except Exception:
        pass
    return base


def save_json_cfg(path: str, data: dict) -> bool:
    """原子写 JSON 配置（先写 .tmp 再替换），失败返回 False 不抛异常。"""
    try:
        d = os.path.dirname(os.path.abspath(path))
        if d and not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 自检
def _selftest() -> int:
    """无窗口冒烟自检：建 root → 应用主题 → 抽查样式 → 销毁。"""
    root = tk.Tk()
    root.withdraw()
    setup_style(root)
    st = ttk.Style(root)
    checks = [
        ("theme=clam", st.theme_use() == "clam"),
        ("font found", bool(FONT_FAMILY)),
        ("Accent.TButton bg", st.lookup("Accent.TButton", "background") == PALETTE["accent"]),
        ("TNotebook.Tab padding", st.lookup("TNotebook.Tab", "padding") is not None),
        ("TEntry fieldbackground", st.lookup("TEntry", "fieldbackground") == PALETTE["surface"]),
        ("listbox_kw keys", set(listbox_kw()) >= {"bg", "selectbackground"}),
        ("text_kw keys", set(text_kw()) >= {"bg", "insertwidth"}),
    ]
    pw = paned(root, "h")
    ok_paned = int(pw.cget("sashwidth")) == 6

    # 弹窗尺寸：断言 popup() **解析出**的像素尺寸（受 SCALE 影响）。
    # 不断言 wm_geometry() —— withdraw 状态下 Tk 恒报 "1x1+0+0"，那是假绿/假红。
    win = popup(root, "selftest", size="small")
    got_popup = getattr(win, "_gs_size", None)
    ok_popup = got_popup == (720, 320)
    win2 = popup(root, "selftest2", w=400, h=200)
    got_popup2 = getattr(win2, "_gs_size", None)
    ok_popup2 = got_popup2 == (400, 200)          # 显式 w/h 必须覆盖 size 档位
    win.destroy()
    win2.destroy()
    root.destroy()

    bad = 0
    for name, ok in (checks
                     + [("PanedWindow sash=6", ok_paned),
                        ("popup size='small' -> 720x320", ok_popup),
                        ("popup 显式 w/h 覆盖档位", ok_popup2)]):
        print(("  [ OK ] " if ok else "  [FAIL] ") + name)
        bad += 0 if ok else 1
    if not ok_popup:
        print("        popup size='small' 实际 =", got_popup)
    if not ok_popup2:
        print("        popup w=400,h=200 实际 =", got_popup2)
    print("FONT_FAMILY =", FONT_FAMILY)
    print("SCALE =", SCALE)
    print("RESULT:", "PASS" if bad == 0 else "FAIL(%d)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_selftest())
