#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gui_sidebar.py —— 统一 GUI 风格的**左侧竖排图标菜单 + 分组页签**组件

配套 `gui_style.py`（见其头部说明）。用户要求：**新项目一律要有侧边栏**。

设计来源：`E:\\AI_workspace\\pyaedt\\aedt_gui.py` 的 `LeftNotebook` 类（415–706 行），
行为逐条对齐，但做了两处**有意的改进**：

1. **图标改为纯 tkinter Canvas 矢量绘制**（20×20 设计坐标系）。
   原实现依赖内嵌 base64 PNG + Pillow，不仅需要第三方库、还得为浅色主题重新着色；
   矢量方案**零资源、零依赖、可任意换色缩放**，效果同为 20×20 描边风。
2. **收起/展开判断用 `winfo_manager()` 而不是 `winfo_ismapped()`**。
   后者在窗口尚未映射时恒返回 0，会让"收起时隐藏文字"在刚建好界面时失灵。

--------------------------------------------------------------------------
用法
--------------------------------------------------------------------------
    import gui_style as gs
    import gui_sidebar as sb

    app = gs.init("我的工具")

    nav = sb.LeftNav(app)                 # 或 gs.sidebar(app)
    nav.pack(fill="both", expand=True)

    p1 = ttk.Frame(nav.body); nav.add(p1, text="总览",   icon="home",  group="常用")
    p2 = ttk.Frame(nav.body); nav.add(p2, text="积分",   icon="battery", group="仿真")
    p3 = ttk.Frame(nav.body); nav.add(p3, text="柱状图", icon="chart", group="仿真")

    nav.layout([("常用", [0]), ("仿真", [1, 2])])   # 可选：显式指定分组顺序
    nav.on_change = lambda i: print("切到", i)

    app.mainloop()

`nav.body` 是内容区容器；**页面要建在 `nav.body` 上**，不要建在 `app` 上。

--------------------------------------------------------------------------
API
--------------------------------------------------------------------------
    LeftNav(master, menu_width=200, collapsed=54, row_h=42, icon_size=18)
        .body            内容区容器（ttk.Frame）
        .add(page, text, icon, group)   -> idx
        .select(idx)     选中（会触发 on_change）
        .layout(order)   重排分组：[(组名, [页索引...]), ...]
        .toggle()        收起 / 展开（带宽度动画）
        .current         当前选中索引
        .on_change       选中回调，签名 fn(idx)

    register_icon(name, prims)   注册自定义图标
    ICON_DEFS                    内置图标定义表（20×20 坐标系）
    ICON_NAMES                    可用图标名列表

`LeftNotebook` 是 `LeftNav` 的别名，方便从 aedt_gui.py 迁移代码。
"""

from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

import gui_style as gs

__all__ = [
    "LeftNav", "LeftNotebook", "IconCanvas",
    "ICON_DEFS", "ICON_NAMES", "register_icon", "draw_icon",
]

# ======================================================================
# 图标：20×20 设计坐标系里的图元表
# ======================================================================
# 图元格式：
#   ("line", x1, y1, x2, y2)
#   ("poly", [(x, y), ...], closed_bool)
#   ("rect", x1, y1, x2, y2)
#   ("oval", x1, y1, x2, y2)
#   ("arc",  x1, y1, x2, y2, start_deg, extent_deg)
#   ("dot",  cx, cy, r)                     实心圆点
ICON_DEFS = {
    "home": [
        ("poly", [(3.5, 9.6), (10, 3.4), (16.5, 9.6)], False),
        ("poly", [(5.6, 9.2), (5.6, 16.6), (14.4, 16.6), (14.4, 9.2)], False),
        ("rect", (8.5, 12.4, 11.5, 16.6)),
    ],
    "chart": [
        ("poly", [(3.6, 3.4), (3.6, 16.6), (16.6, 16.6)], False),
        ("poly", [(5.4, 13.4), (8.6, 9.4), (11.4, 12.0), (16.0, 6.2)], False),
        ("dot", 8.6, 9.4, 1.15),
        ("dot", 11.4, 12.0, 1.15),
    ],
    "layers": [
        ("poly", [(10, 3.1), (17, 6.9), (10, 10.7), (3, 6.9)], True),
        ("poly", [(3, 10.7), (10, 14.5), (17, 10.7)], False),
        ("poly", [(3, 13.7), (10, 17.5), (17, 13.7)], False),
    ],
    "code": [
        ("poly", [(8.0, 6.0), (3.6, 10.0), (8.0, 14.0)], False),
        ("poly", [(12.0, 6.0), (16.4, 10.0), (12.0, 14.0)], False),
        ("line", 11.2, 4.4, 8.8, 15.6),
    ],
    "equalizer": [
        ("line", 5.0, 4.4, 5.0, 15.6),
        ("dot", 5.0, 8.2, 1.3),
        ("line", 10.0, 4.4, 10.0, 15.6),
        ("dot", 10.0, 12.6, 1.3),
        ("line", 15.0, 4.4, 15.0, 15.6),
        ("dot", 15.0, 6.6, 1.3),
    ],
    "battery": [
        ("rect", (2.6, 7.4, 11.6, 13.6)),
        ("rect", (11.6, 9.4, 13.4, 11.6)),
        ("line", 16.3, 5.6, 16.3, 9.6),
        ("dot", 16.3, 11.9, 1.0),
    ],
    "power": [
        ("arc", 3.4, 4.4, 16.6, 17.6, 110, 320),
        ("line", 10.0, 2.4, 10.0, 9.0),
    ],
    "signal": [
        ("rect", (3.4, 13.4, 6.4, 17.0)),
        ("rect", (8.25, 10.4, 11.25, 17.0)),
        ("rect", (13.1, 7.4, 16.1, 17.0)),
    ],
    "mug": [
        ("poly", [(5.0, 7.4), (13.8, 7.4), (12.9, 16.2), (5.9, 16.2)], True),
        ("arc", 12.6, 9.0, 17.4, 13.8, 270, 180),
        ("line", 9.0, 2.6, 9.0, 5.6),
        ("line", 12.0, 3.6, 12.0, 5.6),
    ],
    "quilt": [
        ("rect", (3.4, 3.4, 16.6, 16.6)),
        ("line", 3.4, 10.0, 16.6, 10.0),
        ("line", 10.0, 3.4, 10.0, 10.0),
        ("line", 7.0, 10.0, 7.0, 16.6),
    ],
    # 开口朝左的圆环：缺口落在 9 点方向（180°）
    "ring": [
        ("arc", 3.4, 3.4, 16.6, 16.6, 210, 300),
    ],
    "settings": [
        ("oval", 7.4, 7.4, 12.6, 12.6),
        ("line", 10.0, 2.4, 10.0, 4.8),
        ("line", 10.0, 15.2, 10.0, 17.6),
        ("line", 2.4, 10.0, 4.8, 10.0),
        ("line", 15.2, 10.0, 17.6, 10.0),
        ("line", 4.7, 4.7, 6.4, 6.4),
        ("line", 13.6, 13.6, 15.3, 15.3),
        ("line", 15.3, 4.7, 13.6, 6.4),
        ("line", 6.4, 13.6, 4.7, 15.3),
    ],
    "trash": [
        ("line", 3.0, 6.0, 17.0, 6.0),
        ("poly", [(8.0, 6.0), (8.0, 3.2), (12.0, 3.2), (12.0, 6.0)], False),
        ("poly", [(5.0, 6.0), (6.0, 17.0), (14.0, 17.0), (15.0, 6.0)], False),
        ("line", 8.4, 9.0, 8.4, 14.4),
        ("line", 11.6, 9.0, 11.6, 14.4),
    ],
    "save": [
        ("poly", [(3.6, 3.6), (13.4, 3.6), (16.4, 6.6), (16.4, 16.4),
                  (3.6, 16.4)], True),
        ("rect", (6.6, 3.6, 13.4, 8.0)),
        ("rect", (6.0, 11.6, 14.0, 16.4)),
    ],
    "search": [
        ("oval", 4.0, 4.0, 13.0, 13.0),
        ("line", 12.6, 12.6, 17.0, 17.0),
    ],
    "file": [
        ("poly", [(5.6, 3.0), (11.4, 3.0), (14.8, 6.4), (14.8, 17.0),
                  (5.6, 17.0)], True),
        ("poly", [(11.4, 3.0), (11.4, 6.4), (14.8, 6.4)], False),
    ],
    "thermo": [
        ("line", 10.0, 3.6, 10.0, 13.0),
        ("oval", 7.0, 12.6, 13.0, 18.2),
    ],
    # 连接器 / 端子：上为塑壳（带缺口标记），下探出 3 根针脚
    "plug": [
        ("rect", (3.2, 3.2, 16.8, 10.0)),
        ("poly", [(14.8, 4.6), (15.4, 6.6), (14.2, 6.6)], True),
        ("line", 10.0, 10.0, 10.0, 16.8),
        ("line", 6.0, 4.6, 6.0, 6.6),
        ("dot", 6.0, 8.6, 0.9),
        ("dot", 10.0, 8.6, 0.9),
        ("dot", 14.0, 8.6, 0.9),
    ],
}

# 未知图标名时的占位图形（同样按主题色描边，不留空白）
_ICON_FALLBACK = [("rect", (4.0, 4.0, 16.0, 16.0)), ("dot", 10.0, 10.0, 1.6)]

# "cil-home" -> "home" 之类的别名归一
ICON_ALIASES = {
    "cil-chart-line": "chart", "cil-view-quilt": "quilt",
    "cil-battery-alert": "battery", "cil-input-power": "power",
    "cil-signal-cellular-3": "signal", "cil-mug-tea": "mug",
    "cil-ring-left": "ring", "cil-layers": "layers",
    "cil-code": "code", "cil-equalizer": "equalizer", "cil-home": "home",
    "cil-plug": "plug", "cil-connector": "plug",
}

ICON_NAMES = sorted(ICON_DEFS)


def register_icon(name: str, prims: list) -> None:
    """注册/覆盖一个图标（prims 为 20×20 坐标系下的图元列表）。"""
    ICON_DEFS[name] = list(prims)


def _resolve(name: str):
    if not name:
        return _ICON_FALLBACK
    if name in ICON_DEFS:
        return ICON_DEFS[name]
    alias = ICON_ALIASES.get(name)
    if alias and alias in ICON_DEFS:
        return ICON_DEFS[alias]
    short = name[4:] if name.startswith("cil-") else name
    return ICON_DEFS.get(short, _ICON_FALLBACK)


def _nums(pr) -> list:
    """把 ('rect', (a,b,c,d)) 与 ('rect', a,b,c,d) 两种写法归一成数值列表。

    图元表里两种写法都允许，注册自定义图标时不必纠结括号。
    """
    out = []
    for v in pr[1:]:
        if isinstance(v, (tuple, list)):
            out.extend(v)
        else:
            out.append(v)
    return out


def draw_icon(canvas: tk.Canvas, name: str, color: str,
              size: int, cx: float, cy: float,
              stroke: float = None) -> int:
    """把图标画到 canvas 上（以 cx, cy 为中心）。返回画出的图元数量。"""
    prims = _resolve(name)
    k = float(size) / 20.0
    ox, oy = cx - size / 2.0, cy - size / 2.0
    w = stroke if stroke else max(1.0, size * 0.085)
    n = 0
    for pr in prims:
        t = pr[0]
        if t == "line":
            x1, y1, x2, y2 = _nums(pr)[:4]
            canvas.create_line(ox + x1 * k, oy + y1 * k,
                               ox + x2 * k, oy + y2 * k,
                               fill=color, width=w,
                               capstyle="round", joinstyle="round")
        elif t == "poly":
            pts, closed = pr[1], (pr[2] if len(pr) > 2 else False)
            flat = []
            for (x, y) in pts:
                flat += [ox + x * k, oy + y * k]
            if closed:
                canvas.create_polygon(flat, outline=color, fill="",
                                      width=w, joinstyle="round")
            else:
                canvas.create_line(flat, fill=color, width=w,
                                   capstyle="round", joinstyle="round")
        elif t == "rect":
            x1, y1, x2, y2 = _nums(pr)[:4]
            canvas.create_rectangle(ox + x1 * k, oy + y1 * k,
                                    ox + x2 * k, oy + y2 * k,
                                    outline=color, fill="", width=w)
        elif t == "oval":
            x1, y1, x2, y2 = _nums(pr)[:4]
            canvas.create_oval(ox + x1 * k, oy + y1 * k,
                               ox + x2 * k, oy + y2 * k,
                               outline=color, fill="", width=w)
        elif t == "arc":
            x1, y1, x2, y2, s, e = _nums(pr)[:6]
            canvas.create_arc(ox + x1 * k, oy + y1 * k,
                              ox + x2 * k, oy + y2 * k,
                              start=s, extent=e, style="arc",
                              outline=color, width=w)
        elif t == "dot":
            dx, dy, r = _nums(pr)[:3]
            px, py, rr = ox + dx * k, oy + dy * k, r * k
            canvas.create_oval(px - rr, py - rr, px + rr, py + rr,
                               fill=color, outline=color, width=0)
        n += 1
    return n


class IconCanvas(tk.Canvas):
    """固定尺寸的图标画布。

    高度直接给足整行高度、图标垂直居中 —— 这样**收起/展开时图标大小与位置
    完全不变**（这是原实现刻意保证的性质，别改成随容器伸缩）。
    """

    def __init__(self, master, name, color, width_px, height_px,
                 size, bg, pad=0, **kw):
        tk.Canvas.__init__(self, master, width=int(width_px),
                           height=int(height_px), bg=bg,
                           highlightthickness=0, bd=0, **kw)
        self._name = name
        self._color = color
        self._size = size
        self._bg = bg
        self._pad = int(pad)
        self._redraw()

    def _redraw(self):
        self.delete("all")
        self.configure(bg=self._bg)
        # 水平位置固定（左留白 _pad，不居中），保证与文字左对齐关系稳定
        cx = self._pad + (self._size / 2.0)
        cy = float(self.cget("height")) / 2.0
        self._count = draw_icon(self, self._name, self._color,
                                self._size, cx, cy)

    def set_style(self, color, bg):
        if color == self._color and bg == self._bg:
            return
        self._color, self._bg = color, bg
        self._redraw()

    def item_count(self):
        return len(self.find_all())


# ======================================================================
# 侧边栏
# ======================================================================
class LeftNav(ttk.Frame):
    """左侧竖排图标菜单 + 右侧内容区，替代 ttk.Notebook。

    行结构 = 指示条 + 图标画布 + 文字 Label，**行高固定**。
    收起时只把文字 Label 从布局里摘掉，图标大小/位置与行高一律不变。
    """

    def __init__(self, master, menu_width=200, collapsed=54, row_h=42,
                 icon_size=18, **kw):
        ttk.Frame.__init__(self, master, **kw)
        p = gs.PALETTE
        s = gs.SCALE
        self.p = p
        self._icon_size = int(icon_size * s)
        self.w_full = int(menu_width * s)
        self.w_collapsed = int(collapsed * s)
        self.row_h = int(row_h * s)          # 固定行高：收起/展开完全一致
        self.opened = True
        self._job = None
        self._pages = []                     # 页面 frame
        self._rows = []                      # [row, ind, icon, lab_t, text, icon_name]
        self._groups = []                    # 分组名（按 layout 指定顺序）
        self._grp_rows = []                  # [frame, lab, rule, tail]
        self._grp_cache = {}                 # 组名 -> 标题行 frame
        self._lead_spacer = None             # 首个"无标题散项"前的细留白
        self._groups_of = {}                 # idx -> group 名
        self.current = None
        self.on_change = None

        self.menu = tk.Frame(self, bg=p["menu_bg"], width=self.w_full,
                             highlightthickness=0)
        self.menu.pack(side="left", fill="y")
        self.menu.pack_propagate(False)      # 宽度由动画控制

        tk.Frame(self, bg=p["border"], width=1).pack(side="left", fill="y")

        self.body = ttk.Frame(self)
        self.body.pack(side="left", fill="both", expand=True)

        self._build_top()

    # ---------------------------------------------------------- 顶栏
    def _build_top(self):
        p, s = self.p, gs.SCALE
        top = tk.Frame(self.menu, bg=p["menu_bg"], height=int(38 * s))
        top.pack(fill="x", side="top")
        top.pack_propagate(False)
        # 刻意用 tk.Button：ttk.Button 在文字变化时会重算 padding，行高会跳
        self.b_toggle = tk.Button(
            top, text="\u2261  收起菜单", command=self.toggle,
            bg=p["menu_bg"], fg=p["menu_fg"], relief="flat", bd=0,
            activebackground=p["menu_hover"], activeforeground=p["accent"],
            font=(gs.FONT_FAMILY, int(11 * s), "bold"),
            anchor="w", padx=int(14 * s), cursor="hand2")
        self.b_toggle.pack(fill="both", expand=True)
        tk.Frame(self.menu, bg=p["border"], height=1).pack(fill="x")

    # ---------------------------------------------------------- 分组标题
    def _add_group(self, name):
        """插入一条分组标题行（小号灰字 + 下细分隔线），不可点击、不参与选中。"""
        p, s = self.p, gs.SCALE
        wrap = tk.Frame(self.menu, bg=p["menu_bg"])
        # 顶部留白，让相邻两组视觉分层（收起态也保留，行距不跳动）
        tk.Frame(wrap, bg=p["menu_bg"], height=int(9 * s)).pack(fill="x")
        lab = tk.Label(wrap, text=name, bg=p["menu_bg"], fg=p["muted"],
                       anchor="w", bd=0, highlightthickness=0,
                       font=(gs.FONT_FAMILY, int(8 * s), "bold"))
        lab.pack(fill="x", padx=(int(13 * s), 0))
        rule = tk.Frame(wrap, bg=p["border"], height=1)
        rule.pack(fill="x", padx=(int(10 * s), int(10 * s)),
                  pady=(int(5 * s), 0))
        tail = tk.Frame(wrap, bg=p["menu_bg"], height=int(4 * s))
        tail.pack(fill="x")
        # [wrap, lab, rule, tail]；收起/展开都不动 pack 顺序，只改文字
        self._grp_rows.append([wrap, lab, rule, tail])
        return wrap

    # ---------------------------------------------------------- 添加页面
    def add(self, child, text="", icon=None, group=None, **kw):
        idx = len(self._rows)
        p, s = self.p, gs.SCALE
        self._pages.append(child)
        text = (text or "").strip()
        self._groups_of[idx] = (group or "")

        row = tk.Frame(self.menu, bg=p["menu_bg"], height=self.row_h,
                       cursor="hand2")
        row.pack(fill="x")
        row.pack_propagate(False)            # 行高不受内容影响

        ind = tk.Frame(row, bg=p["menu_bg"], width=int(3 * s))
        ind.pack(side="left", fill="y")

        # 图标：宽度写死（左留白 + 图标宽），收起/展开位置完全一致
        pad = int(10 * s)
        ican = IconCanvas(row, icon, p["menu_fg"],
                          width_px=pad + self._icon_size,
                          height_px=self.row_h, size=self._icon_size,
                          bg=p["menu_bg"], pad=pad, cursor="hand2")
        ican.pack(side="left")

        # 文字：收起时整块 pack_forget，不影响图标
        lab_t = tk.Label(row, text=" " + text, bg=p["menu_bg"],
                         fg=p["menu_fg"], anchor="w", bd=0,
                         highlightthickness=0, cursor="hand2",
                         font=(gs.FONT_FAMILY, int(10 * s)))
        lab_t.pack(side="left", fill="y", padx=(int(8 * s), 0))

        for wgt in (row, ind, ican, lab_t):
            wgt.bind("<Button-1>", lambda e, i=idx: self.select(i))
            wgt.bind("<Enter>", lambda e, i=idx: self._hover(i, True))
            wgt.bind("<Leave>", lambda e, i=idx: self._hover(i, False))

        self._rows.append([row, ind, ican, lab_t, text, icon])
        child.pack_forget()
        if idx == 0:
            self.select(0)
        # 立刻按 add 顺序重排一次：这样**只 add、不调 layout() 也能正确显示分组**，
        # 避免"传了 group 却看不到分组标题"这个坑。
        self._repack()
        return idx

    # ---------------------------------------------------------- 选中 / 悬停
    def select(self, idx):
        if not (0 <= idx < len(self._pages)):
            return
        p, s = self.p, gs.SCALE
        for i, r in enumerate(self._rows):
            row, ind, ican, lab_t, text, icon = r
            on = (i == idx)
            bg = p["accent_soft"] if on else p["menu_bg"]
            fg = p["accent"] if on else p["menu_fg"]
            row.configure(bg=bg)
            ican.set_style(fg, bg)
            lab_t.configure(bg=bg, fg=fg,
                            font=(gs.FONT_FAMILY, int(10 * s),
                                  "bold" if on else "normal"))
            ind.configure(bg=p["accent"] if on else p["menu_bg"])
        for i, pg in enumerate(self._pages):
            if i == idx:
                pg.pack(fill="both", expand=True)
            else:
                pg.pack_forget()
        self.current = idx
        if self.on_change:
            self.on_change(idx)

    def _hover(self, idx, on):
        if not (0 <= idx < len(self._rows)) or idx == self.current:
            return                            # 选中态保持不变
        row, ind, ican, lab_t, text, icon = self._rows[idx]
        bg = self.p["menu_hover"] if on else self.p["menu_bg"]
        row.configure(bg=bg)
        ican.set_style(ican._color, bg)
        lab_t.configure(bg=bg)

    # ---------------------------------------------------------- 收起 / 展开
    def toggle(self):
        self.opened = not self.opened
        self._set_text(self.opened)          # 只动文字
        self._animate(self.w_full if self.opened else self.w_collapsed)

    def _set_text(self, show):
        """收起 = 只把文字 Label 摘掉；图标大小/位置、行高都不变。

        用 `winfo_manager()` 判断是否已布局（返回 '' 表示未 pack）。
        原实现用 `winfo_ismapped()`，窗口未映射时恒为 0，会失灵。
        """
        for grp in self._grp_rows:
            wrap, lab, rule, tail = grp
            if not hasattr(lab, "_full_name"):
                lab._full_name = lab.cget("text")
            want = lab._full_name if show else ""
            if str(lab.cget("text")) != want:
                lab.configure(text=want)
        for row, ind, ican, lab_t, text, icon in self._rows:
            managed = bool(lab_t.winfo_manager())
            if show and not managed:
                lab_t.pack(side="left", fill="y",
                           padx=(int(8 * gs.SCALE), 0))
            elif not show and managed:
                lab_t.pack_forget()
        try:
            self.b_toggle.configure(
                text=("\u2261  收起菜单") if show else "\u2261")
        except Exception:
            pass

    # ---------------------------------------------------------- 分组重排
    def layout(self, order):
        """按 order 重排菜单：[(组名, [页索引...]), ...]；组名空串 = 无标题散项。

        只影响**视觉顺序**；`_pages` / `_rows` 的索引一律不变，
        所以 `select(idx)` 与 `on_change` 的调用方逻辑不受影响。
        """
        want, used = [], set()
        for gname, idxs in order:
            for i in idxs:
                if i in used or not (0 <= i < len(self._rows)):
                    continue
                used.add(i)
                want.append((gname, i))
        # 未被 order 覆盖的页按原顺序追加，行为可预期
        for i in range(len(self._rows)):
            if i not in used:
                want.append((self._groups_of.get(i, ""), i))
        self._repack(want)

    def _repack(self, want=None):
        """按 want=[(组名, 页索引), ...] 重排菜单；want=None 则按 _rows 原顺序。

        分组标题按"首次出现顺序"重建；组名变化处插入标题。
        若首个条目是无标题散项，先垫一条细留白，避免与顶栏贴死。
        """
        p, s = self.p, gs.SCALE
        if want is None:
            want = [(self._groups_of.get(i, ""), i)
                    for i in range(len(self._rows))]

        # 1) 先整体摘掉，避免 pack 顺序残留
        for r in self._rows:
            r[0].pack_forget()
        for grp in list(self._grp_rows):
            grp[0].pack_forget()
        if self._lead_spacer is not None:
            self._lead_spacer.pack_forget()

        # 2) 重建分组标题行
        titles = []
        for gname, _i in want:
            if gname and gname not in titles:
                titles.append(gname)
        self._grp_rows = []
        self._grp_cache = {}
        for gname in titles:
            self._grp_cache[gname] = self._add_group(gname)
        self._groups = titles

        # 3) 按 want 顺序重新 pack
        if want and not want[0][0]:
            if self._lead_spacer is None:
                self._lead_spacer = tk.Frame(self.menu, bg=p["menu_bg"],
                                             height=int(9 * s))
            self._lead_spacer.pack(fill="x")
        cur = ""
        for gname, i in want:
            if gname != cur:
                cur = gname
                if gname:
                    self._grp_cache[gname].pack(fill="x")
            self._rows[i][0].pack(fill="x")
        self._set_text(self.opened)

    # ---------------------------------------------------------- 动画
    def _animate(self, target):
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        cur = self.menu.winfo_width() or (
            self.w_full if self.opened else self.w_collapsed)
        step = max(8, int(18 * gs.SCALE))

        def tick():
            nonlocal cur
            if cur == target:
                self._job = None
                return
            cur = (min(target, cur + step) if cur < target
                   else max(target, cur - step))
            self.menu.configure(width=cur)
            self._job = self.after(8, tick)

        tick()


LeftNotebook = LeftNav   # 兼容 aedt_gui.py 里的旧类名


# ======================================================================
# 自检
# ======================================================================
def _selftest() -> int:
    """无窗口自检：建侧边栏 → 加页 → 切页 → 收起 → 重排 → 断言。"""
    root = tk.Tk()
    root.withdraw()
    gs.setup_style(root)

    nav = LeftNav(root)
    pages = []
    for i, (t, ic, gr) in enumerate([
            ("总览", "home", "仿真后处理"),
            ("损耗柱状图", "chart", "仿真后处理"),
            ("电流积分", "battery", "仿真后处理"),
            ("Winding 管理", "ring", "仿真模型处理"),
            ("温度赋值", "thermo", "仿真模型处理")]):
        pg = ttk.Frame(nav.body)
        pages.append(pg)
        nav.add(pg, text=t, icon=ic, group=gr)

    checks = []

    def chk(name, cond):
        checks.append((name, bool(cond)))

    chk("5 行已建", len(nav._rows) == 5)
    chk("默认选中第 0 页", nav.current == 0)
    chk("第 0 页已布局", pages[0].winfo_manager() == "pack")
    chk("第 1 页未布局", pages[1].winfo_manager() == "")

    r0 = nav._rows[0]
    chk("选中行指示条=accent", str(r0[1].cget("bg")) == gs.PALETTE["accent"])
    chk("未选中行指示条=menu_bg",
        str(nav._rows[1][1].cget("bg")) == gs.PALETTE["menu_bg"])
    chk("选中行文字=accent",
        str(r0[3].cget("fg")) == gs.PALETTE["accent"])

    # 图标：必须真的画出了图元（阴性对照会把这个打掉）
    chk("图标已绘制(第0行)", r0[2].item_count() >= 2)
    chk("图标已绘制(第1行)", nav._rows[1][2].item_count() >= 2)
    # 未知图标名必须走占位图形而不是抛异常
    nav.add(ttk.Frame(nav.body), text="未知图标", icon="cil-not-exist-xyz")
    chk("未知图标名有占位图", nav._rows[5][2].item_count() >= 2)

    # 切页
    nav.select(2)
    chk("切到第 2 页后 current=2", nav.current == 2)
    chk("第 2 页已布局", pages[2].winfo_manager() == "pack")
    chk("第 0 页已收起", pages[0].winfo_manager() == "")
    chk("切页后旧指示条复位",
        str(nav._rows[0][1].cget("bg")) == gs.PALETTE["menu_bg"])

    # 收起 / 展开：文字隐藏，图标与行高不变
    h_before = nav.row_h
    ic_before = nav._rows[0][2].item_count()
    nav.toggle()
    chk("收起后 opened=False", nav.opened is False)
    chk("收起后文字已摘除", nav._rows[0][3].winfo_manager() == "")
    chk("收起后分组标题清空",
        str(nav._grp_rows[0][1].cget("text")) == "")
    chk("收起后图标数量不变", nav._rows[0][2].item_count() == ic_before)
    chk("收起后行高不变", nav.row_h == h_before)
    nav.toggle()
    chk("展开后文字回来", nav._rows[0][3].winfo_manager() == "pack")
    chk("展开后分组标题恢复",
        str(nav._grp_rows[0][1].cget("text")) == "仿真后处理")
    chk("展开后宽度目标 = w_full", nav.w_full == int(200 * gs.SCALE))

    # 分组重排
    nav.layout([("仿真模型处理", [3, 4]), ("仿真后处理", [0, 1, 2])])
    chk("重排后分组顺序变了", nav._groups == ["仿真模型处理", "仿真后处理"])
    chk("重排后索引不变(第3页仍是 Winding)",
        nav._rows[3][4] == "Winding 管理")

    root.destroy()

    bad = 0
    for name, ok in checks:
        print(("  [ OK ] " if ok else "  [FAIL] ") + name)
        bad += 0 if ok else 1
    print("ICON_NAMES(%d) = %s" % (len(ICON_NAMES), ", ".join(ICON_NAMES)))
    print("RESULT:", "PASS" if bad == 0 else "FAIL(%d)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(_selftest())
