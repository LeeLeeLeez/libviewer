# -*- coding: utf-8 -*-
"""
元件库查看器 (LibViewer)
=========================
左侧按物料类别分 tab，右侧显示该类别的「原理图库 / PCB库」：
  · 原理图库：料号 / MPN / 厂商 / 封装 / spec
  · PCB库   ：封装名
  · 两者都有搜索框（实时过滤，匹配任意列）

数据来源：lib_cache.json —— 由本机【离线】解析库文件生成（build_cache.py），
全程不依赖 AD 是否在运行。

读取链路（2026-09-24 重构，方案 A）：
    ① plainize.py 扫全库，把 DLP 密文正本用 History 明文快照**转成明文正本**；
    ② build_cache.py **纯离线**解析（lib_reader，0.07 s/全库 961 符号）。

为什么要先转明文：AD 保存过的库落盘即 DLP 密文，离线读不了；走 AD 桥接要
逐件解密（1.44 s/件 → 961 符号约 1384 s）。转明后离线读快约 20000×，
且 resolve_sources 永远命中「正本明文」，不会再静默回退到过时快照。

🔴🔴 严禁调用 app_save_all —— 它会把明文重新打成密文。

本 GUI 只依赖 tkinter + 标准库 —— 零第三方依赖，便于打包。
"""
import os
import sys
import json
import queue
import threading
import subprocess
import time
import traceback
import tkinter as tk
from tkinter import ttk

# 🔴🔴 打包(frozen) 后路径分两类，切勿混用：
#   HERE = 可写/持久目录 —— 放 lib_cache.json、build_cache.py、_del_worker.py
#          （这些要留在 exe 旁边，供外部 venv 进程调用）
#   RES  = 只读资源目录 —— 打进 exe 的文件（appicon.ico 等）
# onefile 下 `__file__` 指向 sys._MEIPASS（每次运行都变），
# 若当成 HERE 用，缓存会写到临时目录、每次启动都丢。
if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
    RES = getattr(sys, '_MEIPASS', HERE)
else:
    HERE = os.path.dirname(os.path.abspath(__file__))
    RES = HERE
sys.path.insert(0, HERE)

import gui_style as gs                                    # noqa: E402
import gui_sidebar as gsb                                 # noqa: E402
import worker_bridge as wb                                # noqa: E402
from delete_page import DeletePage                        # noqa: E402

APP_ID = 'LibViewer.ComponentLibrary'
ICON_PNG = os.path.join(RES, 'appicon.png')    # 打包资源目录
ICON_ICO = os.path.join(RES, 'appicon.ico')
_ICON_REF = []          # PhotoImage 必须留引用，否则被 GC 掉、图标会消失

CACHE = os.path.join(HERE, 'lib_cache.json')
BUILDER = os.path.join(HERE, 'build_cache.py')
# 构建缓存需要 olefile → 用装了它的隔离 venv（GUI 自身不需要）
BUILD_PY = r'C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0


def _utf8_env():
    """调子进程时注入 UTF-8 环境变量。

    🔴🔴 本机 Windows 默认 locale = cp936(GBK)。用户双击 .cmd 启动
    GUI 时环境里没有 PYTHONUTF8 / PYTHONIOENCODING，子进程（build_cache.py）
    的 print 就按 GBK 输出，而本文件用 encoding='utf-8' 解码
    → 类别名「01电阻」等全乱码。在此钉死。
    """
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


PAL = gs.PALETTE

# 类别 → (分组, 图标)
META = {
    '01电阻':   ('无源器件', 'equalizer'),
    '02电容':   ('无源器件', 'battery'),
    '09电感':   ('无源器件', 'ring'),
    '05二极管': ('半导体',   'signal'),
    '06晶体管': ('半导体',   'power'),
    '07IC':     ('集成电路', 'code'),
    '10其他':   ('其他',     'file'),
    '25端子':   ('结构件',   'plug'),
}
# 显示顺序；未列入的类别会自动追加到末尾（见 _build），
# 因此新增类别只需在 lib_reader.CATEGORIES 里登记即可，不必改这里。
ORDER = ['01电阻', '02电容', '09电感', '05二极管', '06晶体管', '07IC',
         '25端子', '10其他']


# ------------------------------------------------------------------ 图标
def set_app_user_model_id():
    """告诉 Windows 本进程的 AppUserModelID。

    不做这一步，任务栏会把窗口算到 pythonw.exe 名下、显示 Python 图标，
    而不是我们的 appicon。
    """
    if os.name != 'nt':
        return False
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        return True
    except Exception:
        return False


def _load_png_file(path):
    """读 .png 并返回 Tk PhotoImage；不可用则返回 None。

    ⚠ 本机 DLP 会对 .png 做透明加密（文件头 255453442d486561 =
       '%TSD-Header-###%'），加密后 Tk 直接报 'couldn't recognize data'。
       所以先验文件头，再交给 Tk —— 避免把密文当图片读。
    """
    try:
        if not path or not os.path.exists(path):
            return None
        with open(path, 'rb') as f:
            if f.read(8) != b'\x89PNG\r\n\x1a\n':
                return None            # DLP 密文 / 非 PNG
        img = tk.PhotoImage(file=path)
        return img if img.width() > 1 else None
    except Exception:
        return None


def apply_icon(root):
    """把「Lib」图标应用到窗口标题栏与任务栏。

    图标字节**内嵌在 appicon_data.py**（base64），不依赖外部图片文件 ——
    因为本机 DLP 会加密 .png（实测 `%TSD-Header`），Tk 就读不了；
    而 .py / .ico 不受影响，所以内嵌是唯一稳的载体。

    两条腿：`iconphoto` 管大图（Alt-Tab、任务栏缩略图），
    `iconbitmap` 用多尺寸 .ico 管小图（任务栏 16×16 才锐利）。
    ⚠ `iconphoto` 会覆盖 `iconbitmap`，所以 iconbitmap 必须放最后。
    任一路径失败都不影响主流程。
    """
    import base64
    ico_ok = png_ok = False

    try:
        import appicon_data as AD
    except Exception:
        AD = None

    # ① 大图：内嵌 PNG 直接喂 Tk（无文件依赖）
    if AD is not None and getattr(AD, 'PNG_B64', ''):
        try:
            img = tk.PhotoImage(data=AD.PNG_B64)
            _ICON_REF.append(img)      # 不留引用会被 GC，图标随即消失
            root.iconphoto(True, img)
            png_ok = True
        except Exception:
            pass
    else:
        img = _load_png_file(ICON_PNG)   # 带 DLP 密文守卫（蚄文会静默失败）
        if img is not None:
            _ICON_REF.append(img)
            root.iconphoto(True, img)
            png_ok = True

    # ② 小图：多尺寸 .ico。优先用内嵌字节落一个临时文件（避开 DLP 对图片的加密）
    cands = []
    if AD is not None and getattr(AD, 'ICO_B64', ''):
        try:
            import tempfile
            p = os.path.join(tempfile.gettempdir(), 'LibViewer_appicon.ico')
            blob = base64.b64decode(AD.ICO_B64)
            try:
                cur = open(p, 'rb').read()
            except OSError:
                cur = None
            if cur != blob:
                with open(p, 'wb') as f:
                    f.write(blob)
            cands.append(p)
        except Exception:
            pass
    if os.path.exists(ICON_ICO):
        cands.append(ICON_ICO)
    for p in cands:
        try:
            root.iconbitmap(p)
            ico_ok = True
            break
        except Exception:
            continue
    if cands:
        try:
            root.iconbitmap(default=cands[0])   # 让 Toplevel 弹窗也继承
        except Exception:
            pass
    return ico_ok, png_ok


# ------------------------------------------------------------------ 样式
def style_tree():
    st = ttk.Style()
    st.configure('Lib.Treeview',
                 background=PAL['surface'],
                 fieldbackground=PAL['surface'],
                 foreground=PAL['text'],
                 rowheight=int(24 * gs.SCALE),
                 borderwidth=0)
    st.configure('Lib.Treeview.Heading',
                 background=PAL['bg'],
                 foreground=PAL['muted'],
                 relief='flat',
                 font=gs.font(9, bold=True))
    st.map('Lib.Treeview',
           background=[('selected', PAL['accent_soft'])],
           foreground=[('selected', PAL['text'])])
    st.map('Lib.Treeview.Heading',
           background=[('active', PAL['bg'])])
    st.configure('Search.TEntry',
                 fieldbackground=PAL['surface'],
                 bordercolor=PAL['border'],
                 lightcolor=PAL['border'],
                 darkcolor=PAL['border'])
    st.configure('Tool.TButton', padding=(10, 3))


# ------------------------------------------------------------------ 分节
class Section(ttk.Frame):
    """一个库分节：标题 + 搜索框 + 表格 + 计数。

    columns: [(表头, 内部键, 宽度px), ...]
    """

    def __init__(self, parent, title, columns, on_open=None):
        super().__init__(parent)
        self.headings = [c[0] for c in columns]
        self.keys = [c[1] for c in columns]
        self.widths = [c[2] for c in columns]
        self.on_open = on_open
        self.rows = []
        self._index = {}
        self.col_vars = {}
        self._last_w = None
        self._build(title)

    def _build(self, title):
        # ── 工具栏第 1 行：标题 …… 计数
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=10, pady=(8, 2))
        ttk.Label(bar, text=title, style='Card.TLabel').pack(side='left')
        self.count = ttk.Label(bar, text='', style='Muted.TLabel')
        self.count.pack(side='right')

        # ── 工具栏第 2 行：提示 …… 清空 / 全表搜索
        bar2 = ttk.Frame(self)
        bar2.pack(fill='x', padx=10, pady=(0, 4))
        ttk.Label(bar2,
                  text='列筛选：双击框选值　空格=且　!词=排除',
                  style='Muted.TLabel').pack(side='left')
        ttk.Button(bar2, text='清空筛选', style='Tool.TButton',
                   command=self.clear_filters).pack(side='right', padx=(8, 0))
        self.var = tk.StringVar()
        self.var.trace_add('write', lambda *_: self.refilter())
        ttk.Entry(bar2, textvariable=self.var, width=22,
                  style='Search.TEntry', font=gs.font(9)).pack(side='right')
        ttk.Label(bar2, text='全表搜索', style='Muted.TLabel').pack(
            side='right', padx=(0, 4))

        wrap = tk.Frame(self, bg=PAL['border'])
        wrap.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        inner = tk.Frame(wrap, bg=PAL['surface'])
        inner.pack(fill='both', expand=True, padx=1, pady=1)

        # ── 每列筛选行（与表头列宽对齐，随列宽同步）
        self.fbar = tk.Frame(inner, bg=PAL['surface'])
        self.fbar.grid(row=0, column=0, sticky='ew')
        for i, k in enumerate(self.keys):
            self.fbar.columnconfigure(i, minsize=int(self.widths[i] * gs.SCALE))
            v = tk.StringVar()
            v.trace_add('write', lambda *_: self.refilter())
            self.col_vars[k] = v
            e = ttk.Entry(self.fbar, textvariable=v,
                          style='Search.TEntry', font=gs.font(8))
            e.grid(row=0, column=i, sticky='ew', padx=1, pady=(2, 1))
            e.bind('<Double-1>', lambda ev, kk=k: self._show_values(kk))

        self.tree = ttk.Treeview(inner, columns=self.keys, show='headings',
                                 style='Lib.Treeview', selectmode='browse')
        last = len(self.keys) - 1
        for i, (k, h, w) in enumerate(zip(self.keys, self.headings, self.widths)):
            self.tree.heading(k, text=h, anchor='w')
            self.tree.column(k, width=int(w * gs.SCALE), anchor='w',
                             stretch=(i == last))
        vs = ttk.Scrollbar(inner, orient='vertical', command=self.tree.yview)
        hs = ttk.Scrollbar(inner, orient='horizontal', command=self.tree.xview)
        self.tree.configure(yscrollcommand=vs.set, xscrollcommand=hs.set)
        self.tree.grid(row=1, column=0, sticky='nsew')
        vs.grid(row=1, column=1, sticky='ns')
        hs.grid(row=2, column=0, sticky='ew')
        inner.rowconfigure(1, weight=1)
        inner.columnconfigure(0, weight=1)

        self.tree.tag_configure('tpl', foreground=PAL['muted'])
        self.tree.bind('<Configure>', self._sync_filter_widths)
        if self.on_open:
            self.tree.bind('<Double-1>', self._open)

    def _sync_filter_widths(self, _e=None):
        """让每列筛选框跟随列宽（用户拖列宽时也同步）。"""
        w = tuple(self.tree.column(k, 'width') for k in self.keys)
        if w == self._last_w:
            return
        self._last_w = w
        for i, ww in enumerate(w):
            self.fbar.columnconfigure(i, minsize=ww)

    # ---------------------------------------------------------- 筛选
    @staticmethod
    def _match_cell(cell, expr):
        """单元格是否满足该列筛选式。
        空格分隔多个条件（全需满足）；`!词` 表示排除该词。"""
        if not expr:
            return True
        low = cell.lower()
        for term in expr.split():
            neg = term.startswith('!')
            t = (term[1:] if neg else term).lower()
            if not t:
                continue
            if (t in low) == neg:      # 含词却要求排除 / 不含词却要求包含
                return False
        return True

    def clear_filters(self):
        for v in self.col_vars.values():
            v.set('')
        self.var.set('')

    def active_filters(self):
        n = sum(1 for v in self.col_vars.values() if v.get().strip())
        return n + (1 if self.var.get().strip() else 0)

    def set_rows(self, rows):
        self.rows = rows
        self._index = {}
        for r in rows:
            self._index[self._key_of(r)] = r
        self.refilter()

    def _vals(self, r):
        """一行的显示值（含「未挂封装」标记），供插入与反查共用。"""
        out = []
        for k in self.keys:
            v = str(r.get(k, '') or '')
            if k == '封装' and not v and r.get('cls') == 'normal':
                v = '⚠ 未挂封装'
            out.append(v)
        return out

    def _key_of(self, r):
        return tuple(self._vals(r))

    def refilter(self):
        g = self.var.get().strip().lower()
        cols = {k: v.get().strip() for k, v in self.col_vars.items()}
        self.tree.delete(*self.tree.get_children())
        n = 0
        for r in self.rows:
            vals = self._vals(r)
            # ① 全表搜索（跨列 OR）
            if g and not any(g in v.lower() for v in vals):
                continue
            # ② 逐列筛选（列内 AND，列间 AND）
            ok = True
            for i, k in enumerate(self.keys):
                if not self._match_cell(vals[i], cols.get(k, '')):
                    ok = False
                    break
            if not ok:
                continue
            self.tree.insert('', 'end', iid=None, values=vals,
                             tags=(('tpl',) if r.get('template') else ()))
            n += 1
        total = len(self.rows)
        nf = self.active_filters()
        tail = '　筛选 %d 项' % nf if nf else ''
        self.count.config(text=(('共 %d 条' % total) if n == total
                                else ('%d / %d 条' % (n, total))) + tail)

    def _show_values(self, key):
        """双击筛选框 → 列出该列去重取值（带出现次数），点选即作为筛选条件。"""
        idx = self.keys.index(key)
        head = self.headings[idx]
        cnt = {}
        for r in self.rows:
            v = str(r.get(key, '') or '') or '(空)'
            cnt[v] = cnt.get(v, 0) + 1
        if not cnt:
            return
        items = sorted(cnt.items(), key=lambda kv: (-kv[1], kv[0]))

        win = gs.popup(self.winfo_toplevel(), '选择「%s」的值' % head,
                       size='small')
        top = ttk.Frame(win)
        top.pack(fill='x', padx=14, pady=(12, 6))
        ttk.Label(top, text='%s　共 %d 个不同取值' % (head, len(items)),
                  style='Title.TLabel').pack(side='left')

        body = tk.Frame(win, bg=PAL['border'])
        body.pack(fill='both', expand=True, padx=14, pady=(0, 8))
        box = tk.Frame(body, bg=PAL['surface'])
        box.pack(fill='both', expand=True, padx=1, pady=1)
        lb = tk.Listbox(box, **gs.listbox_kw())
        vs = ttk.Scrollbar(box, orient='vertical', command=lb.yview)
        lb.configure(yscrollcommand=vs.set)
        lb.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        for v, c in items:
            lb.insert('end', '%s    (%d)' % (v, c))

        def pick(*_a):
            s = lb.curselection()
            if not s:
                return
            val = items[s[0]][0]
            self.col_vars[key].set('' if val == '(空)' else val)
            win.destroy()

        lb.bind('<Double-1>', pick)
        lb.bind('<Return>', pick)
        bar = ttk.Frame(win)
        bar.pack(fill='x', padx=14, pady=(0, 12))
        ttk.Button(bar, text='选用选中值', style='Accent.TButton',
                   command=pick).pack(side='right')
        ttk.Button(bar, text='清空该列',
                   command=lambda: (self.col_vars[key].set(''),
                                    win.destroy())).pack(side='right', padx=(0, 8))
        lb.focus_set()
        if items:
            lb.selection_set(0)

    def _open(self, _e):
        sel = self.tree.selection()
        if not sel:
            return
        vals = self.tree.item(sel[0], 'values')
        full = self._index.get(tuple(str(v) for v in vals))
        self.on_open(full if full is not None else dict(zip(self.keys, vals)))


# ==================================================================== Home
class HomePage(ttk.Frame):
    """首页：各类别库的目录一览 + 「读取库数据」。

    读取流程（固化自 2026-09-24 的占位封装清理工作）：
      ① 先探活 AD 轮询（直接拿真实库调 lib_get_footprints）——
         不通就弹窗提示用户先开 AD 轮询，绝不硬跑；
      ② 通了才用 build_cache_bridge.py 经 AD 读**实时**内容重建 lib_cache.json。
         （不用 build_cache.py：它遇 DLP 密文会静默回退到 History 快照，
           导致界面显示过时数据——本轮血案。）
    """

    TITLE = 'Home　—　物料库总览'

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._busy = False
        self._build()
        # 🔴 启动后自查「库文件是否比缓存新」——
        #    查看器是**缓存驱动**：改完库不重建缓存，界面时间就一直是旧的。
        #    2026-10-15 用户连遇两次，故加这道提示（只提示，不自动写文件）。
        try:
            self.after(700, self._check_stale)
        except Exception:
            pass

    def _check_stale(self):
        """比对缓存里记的 mtime 与库文件实际 mtime；不一致就提示重建。

        缓存里 `sch_src.mtime` / `pcb_src.mtime` 是**字符串**（到分钟），
        所以这里也按同样格式生成再比。
        """
        try:
            import plainize as _pz
            cache_p = os.path.join(HERE, 'lib_cache.json')
            if not os.path.isfile(cache_p):
                return
            with open(cache_p, encoding='utf-8') as f:
                c = json.load(f)
            base = _pz.get_base()
            stale = []
            for e in c.get('categories') or []:
                folder, stem = e.get('folder'), e.get('stem')
                if not folder or not stem:
                    continue
                for ext, key in (('.SchLib', 'sch_src'),
                                 ('.PcbLib', 'pcb_src')):
                    src = e.get(key) or {}
                    mt = src.get('mtime')
                    p = os.path.join(base, folder, stem + ext)
                    if not mt or not os.path.isfile(p):
                        continue
                    real = time.strftime(
                        '%Y-%m-%d %H:%M',
                        time.localtime(os.path.getmtime(p)))
                    if real != mt:
                        stale.append('%s%s：缓存 %s → 实际 %s'
                                     % (stem, ext, mt, real))
            if stale:
                self._log('')
                self._log('⚠ 有 %d 个库文件比缓存新'
                          ' —— 下面显示的是旧数据：'
                          % len(stale))
                for x in stale[:12]:
                    self._log('    ' + x)
                if len(stale) > 12:
                    self._log('    …（还有 %d 个）' % (len(stale) - 12))
                self._log('    → 点【读取库数据】重建缓存即可。')
                try:
                    self.lbl_hint.configure(
                        text='⚠ 库文件已更新，请点【读取库数据】',
                        foreground='#D97706')
                except Exception:
                    pass
        except Exception as e:
            try:
                self._log('（库文件变化检测失败：%r）' % e)
            except Exception:
                pass

    # ------------------------------------------------------------ 构建
    def _build(self):
        head = tk.Frame(self, bg=PAL['bg'])
        head.pack(fill='x', padx=14, pady=(12, 0))
        ttk.Label(head, text=self.TITLE, style='Title.TLabel').pack(side='left')

        # 库根目录（数据从哪来）
        self.lbl_base = ttk.Label(self, text='', style='Muted.TLabel')
        self.lbl_base.pack(anchor='w', padx=14, pady=(4, 2))

        btns = tk.Frame(self, bg=PAL['bg'])
        btns.pack(fill='x', padx=14, pady=(2, 6))
        self.btn_fetch = ttk.Button(btns, text='读取库数据',
                                    style='Accent.TButton',
                                    command=self.on_fetch)
        self.btn_fetch.pack(side='left')
        self.btn_scan = ttk.Button(btns, text='扫描密文',
                                   style='Tool.TButton',
                                   command=self.on_scan)
        self.btn_scan.pack(side='left', padx=(8, 0))
        self.btn_open = ttk.Button(btns, text='打开库目录',
                                   style='Tool.TButton',
                                   command=self.on_open_dir)
        self.btn_open.pack(side='left', padx=(8, 0))
        self.lbl_hint = ttk.Label(btns, text='密文自动转明 → 纯离线解析（不用开 AD）',
                                  style='Muted.TLabel')
        self.lbl_hint.pack(side='right')

        # ── 上下可拖拽分栏：上=日志，下=目录表
        pw = gs.paned(self, orient='v')
        pw.pack(fill='both', expand=True, padx=10, pady=(0, 8))

        lf = gs.card(pw, '读取日志')
        box = tk.Frame(lf, bg=PAL['surface'])
        box.pack(fill='both', expand=True, padx=8, pady=(2, 8))
        self.txt = tk.Text(box, height=7, wrap='none', **gs.text_kw())
        vs = ttk.Scrollbar(box, orient='vertical', command=self.txt.yview)
        self.txt.configure(yscrollcommand=vs.set)
        vs.pack(side='right', fill='y')
        self.txt.pack(side='left', fill='both', expand=True)
        pw.add(lf, minsize=int(120 * gs.SCALE))

        pf = gs.card(pw)
        self.tbl = Section(
            pf, '物料库目录　（双击一行可在文件管理器中打开）',
            [('类别', 'cat', 100), ('目录', 'dir', 150),
             ('原理图库 (SchLib)', 'sch', 280),
             ('PCB 库 (PcbLib)', 'pcb', 280),
             ('符号', 'n_sch', 55), ('封装', 'n_pcb', 55)],
            on_open=self._open_row)
        self.tbl.pack(fill='both', expand=True, padx=6, pady=(2, 6))
        pw.add(pf, minsize=int(180 * gs.SCALE))

        self._log('就绪。点「读取库数据」= 密文自动转明 → 离线解析（不用开 AD）。')
        self.refresh()

    # ------------------------------------------------------------ 数据
    def _categories(self):
        """类别单一真源 = lib_reader.CATEGORIES（勿在别处写死）。

        🔴 不能直接 import lib_reader：它顶层 `import olefile`，而 GUI 跑在
           系统 Python 312（有 tkinter、无 olefile）→ ModuleNotFoundError
           → 本方法走 except 返回空列表 → Home 目录表整片空白。
        ⇒ 改走 plainize 的【零依赖】get_categories()/get_base()
          （plainize 内部只在解析 OLE 时才需要 olefile，且是延迟 import）。
        """
        try:
            import plainize
            return list(plainize.get_categories()), plainize.get_base()
        except Exception as e:
            # 兜底：直接读 lib_reader 模块源码里的 CATEGORIES 字面量
            try:
                import importlib, re as _re
                src = open(os.path.join(HERE, 'lib_reader.py'),
                           encoding='utf-8').read()
                m = _re.search(r'CATEGORIES\s*=\s*(\[.*?\])\s*\n',
                               src, _re.S)
                cats = eval(m.group(1)) if m else []
                base = getattr(plainize, 'BASE_DEFAULT', None) or \
                    r'E:\MaterialLib\AD_Lib'
                return list(cats), base
            except Exception as e2:
                return [], '(读取失败: %r / %r)' % (e, e2)

    def refresh(self):
        cats, base = self._categories()
        self.lbl_base.config(text='库根目录：%s' % base)

        # 缓存里的实际计数（显示 符号/封装 数）
        counts = {}
        try:
            with open(CACHE, encoding='utf-8') as f:
                d = json.load(f)
            for c in d.get('categories', []):
                counts[c['cat']] = (len(c.get('sch', [])), len(c.get('pcb', [])))
        except Exception:
            pass

        rows = []
        for cat, folder, stem in cats:
            dd = os.path.join(base, folder)
            sch = os.path.join(dd, stem + '.SchLib')
            pcb = os.path.join(dd, stem + '.PcbLib')
            n_s, n_p = counts.get(cat, ('', ''))
            rows.append({
                'cat': cat,
                'dir': dd,
                'sch': self._file_text(sch),
                'pcb': self._file_text(pcb),
                'n_sch': '' if n_s == '' else str(n_s),
                'n_pcb': '' if n_p == '' else str(n_p),
                '_dir': dd,
            })
        self.tbl.set_rows(rows)
        return len(rows)

    @staticmethod
    def _file_text(p):
        if not os.path.exists(p):
            return '（缺失）'
        try:
            with open(p, 'rb') as f:
                hd = f.read(8)
            if hd == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                tag = '明文'
            elif hd[:5] == b'%TSD-':
                tag = 'DLP密文'
            else:
                tag = '未知'
            return '%s　%d KB　%s' % (os.path.basename(p),
                                     os.path.getsize(p) // 1024, tag)
        except Exception as e:
            return '读取失败：%r' % e

    # ------------------------------------------------------------ 交互
    def _log(self, msg):
        try:
            self.txt.insert('end', msg + '\n')
            self.txt.see('end')
            self.txt.update_idletasks()
        except Exception:
            pass

    def _open_row(self, d):
        p = (d or {}).get('_dir', '')
        if p and os.path.isdir(p):
            self._open_path(p)

    def on_open_dir(self):
        _, base = self._categories()
        if os.path.isdir(base):
            self._open_path(base)
        else:
            self._log('目录不存在：%s' % base)

    @staticmethod
    def _open_path(p):
        try:
            if os.name == 'nt':
                os.startfile(p)                      # noqa: S606
            elif sys.platform == 'darwin':
                subprocess.Popen(['open', p])
            else:
                subprocess.Popen(['xdg-open', p])
        except Exception as e:
            print('打开失败:', e)

    def _set_busy(self, busy):
        self._busy = busy
        st = 'disabled' if busy else 'normal'
        for b in (self.btn_fetch, self.btn_scan, self.btn_open):
            try:
                b.config(state=st)
            except Exception:
                pass
        try:
            self.app.root.config(cursor='watch' if busy else '')
            self.app.root.update_idletasks()
        except Exception:
            pass

    def _scan_cipher(self):
        """扫全库，返回 ({'folder|ext': (cat, path, kind)}, base)。

        🔴 只用 plainize 的**零依赖**接口（get_base / get_categories / kind）——
           不要在 GUI 里 import lib_reader：它顶层 `import olefile`，
           而 GUI 跑在系统 Python（有 tkinter、无 olefile）→ ModuleNotFoundError。
        """
        import importlib
        import plainize
        # 🔴 与 on_fetch 同理：frozen 下没必要 reload（代码已固化），
        #    且不该让它的异常影响主流程。
        if not getattr(sys, 'frozen', False):
            try:
                importlib.reload(plainize)
            except Exception:
                pass
        base = plainize.get_base()
        out = {}
        for cat, folder, stem in plainize.get_categories():
            for ext in plainize.EXTS:
                p = os.path.join(base, folder, stem + ext)
                out[(folder, ext)] = (cat, p, plainize.kind(p))
        return out, base

    def on_scan(self):
        """只扫描并报告，不做任何写入。"""
        if self._busy:
            return
        self._log('-' * 46)
        self._log('扫描各库 SchLib 状态…')
        self._set_busy(True)
        try:
            got, base = self._scan_cipher()
        except Exception as e:
            self._log('✘ 扫描失败：%r' % e)
            self._set_busy(False)
            return
        self._set_busy(False)

        n_cipher = 0
        by_cat = {}
        for (folder, ext), (cat, p, k) in got.items():
            by_cat.setdefault(cat, {})[ext] = k
            if k == 'cipher':
                n_cipher += 1
        short = {'.SchLib': 'Sch', '.PcbLib': 'Pcb'}
        for cat, m in by_cat.items():
            parts = []
            for ext in ('.SchLib', '.PcbLib'):
                k = m.get(ext, 'missing')
                parts.append('%s:%s' % (short[ext],
                                        {'plain': '明文', 'cipher': '密文⚠',
                                         'missing': '-', 'unknown': '?'}.get(k, k)))
            self._log('   %-10s %s' % (cat, '  '.join(parts)))
        self._log('   小结：共 %d 个库文件，其中密文 %d 个'
                  % (len(got), n_cipher))
        if n_cipher:
            self._log('   → 点「读取库数据」即可自动转明（有快照的秒转）。')
        else:
            self._log('   → 全为明文，点「读取库数据」直接离线解析。')

    def on_fetch(self):
        """一键：① 密文自动转明 → ② 纯离线重建缓存 → ③ 刷新界面。"""
        if self._busy:
            return
        self._log('=' * 46)
        self._log('开始读取库数据（密转明 → 离线解析）…')
        self._set_busy(True)
        try:
            # ---- ① 密转明 ----
            # 🔴🔴 exe(frozen) 下**不能** importlib.reload：
            #    PyInstaller 的模块从 PYZ 里加载，reload 会抛异常；
            #    而它在 try 块**开头**，一炸就把整个 on_fetch 打断
            #    → 表现为「点了读取库数据，既不转明也不重建」
            #    （实测：源码态能转、打包成 exe 后转不了）。
            #    reload 本来只是开发时改了 plainize.py 想立刻生效用的，
            #    打包后代码已固化，无需也不可能 reload。
            import importlib
            import plainize
            if not getattr(sys, 'frozen', False):
                try:
                    importlib.reload(plainize)
                except Exception as _e:
                    self._log('   （plainize 重载失败，忽略：%r）' % _e)

            self._log('① 检查并转换密文库…')
            try:
                rep = plainize.plainize_all(log=self._log)
            except Exception as e:
                self._log('   内部错误：%r' % e)
                rep = None

            if rep is not None:
                for line in plainize.format_report(rep):
                    self._log('   ' + line)

            # 有库缺快照 → 明确提示，但不阻断（其余照样能读）
            blocked = (rep or {}).get('no_snapshot') or []
            if blocked:
                # 🔴 HomePage 是 Frame，没有 .root —— 必须用 self.app.root。
                #    之前写成 self.root.after 直接 AttributeError，
                #    炸掉整个 on_fetch（build_cache 从未执行）→ 用户看到「重建失败」。
                #    弹窗失败也不该中断主流程，故整体加保护。
                try:
                    self.app.root.after(
                        0, lambda b=blocked: self._warn_no_snapshot(b))
                except Exception as _e:
                    self._log('   （提示窗弹出失败：%r）' % _e)

            # 仍有密文残留（无快照 / 转换失败）→ 它们离线读不了
            failed = (rep or {}).get('failed') or []
            if rep is None or failed:
                self._log('   ⚠ 有类别未能转明，离线解析将跳过它们。')

            # ---- ② 离线重建 ----
            self._log('② 离线解析并重建缓存…')
            # ② 走 worker_bridge：源码态=venv+脚本，打包态=exe 自任 worker
            _rc, _out, _err = wb.run('build', timeout=600)
            p = type('X', (), {'returncode': _rc, 'stdout': _out,
                               'stderr': _err})()
            ok = (_rc == 0)
        except Exception as ex:
            ok = False
            p = type('X', (), {'stdout': '', 'stderr': str(ex),
                               'returncode': -1})()
        finally:
            self._set_busy(False)

        if not ok:
            self._log('✘ 重建失败：%s' % ((p.stderr or p.stdout or '')[:200]))
            self.status.config(text='重建失败')
            return

        # 把 build_cache.py 的逐行输出贴进日志（含 sch/pcb 来源，便于看有没有回退快照）
        for line in (p.stdout or '').strip().splitlines():
            self._log('   ' + line)

        # ---- ③ 刷新 ----
        n = self.refresh()
        self._log('✔ 完成，已刷新本页 %d 行。' % n)
        try:
            self.app.rebuild(reload_only=True)
        except Exception as e:
            self._log('   （分类页刷新失败：%r）' % e)

    def _warn_no_snapshot(self, blocked):
        """缺 History 快照时的提示窗（说明要手动让 AD 打开这些库）。"""
        names = '、'.join(r['cat'] for r in blocked)
        paths = '\n'.join('    ' + r['path'] for r in blocked)
        # 🔴🔴 打包成 exe 后这个弹窗最容易误导人：
        #    exe **不在 DLP 白名单里**，读到的就是原始密文
        #    zip（head 25545344…），所以**即使 History 里有快照也读不出来**。
        #    而 `python.exe` 在白名单里，读同一文件会被自动解密。
        #    这就是「源码态/命令行能转、exe 转不了」的真因（2026-10-14 实测）。
        if getattr(sys, 'frozen', False):
            tip = (
                '🔴 当前运行的是**打包后的 exe**：它不在 DLP 白名单里，'
                '读到的是**原始密文**，\n'
                '    所以即使 History 里有快照也读不出来。\n\n'
                '解决办法（推荐第一条）：\n'
                '  ① 用配套工具「**启动密文转明文.cmd**」'
                '（它跑 Python，在白名单里）：\n'
                '     选中上面这些库所在的**文件夹**，点「开始转换」；\n'
                '     转完再回来点一次「读取库数据」即可。\n'
                '  ② 若提示「History 里没有快照」，先在 AD 里'
                '**打开该库 → File | Save**'
                '（只打开不保存不会产生快照），再做 ①。\n\n'
                '⚠ 转明后请勿在 AD 里执行「Save All」——会把明文重新加密。'
            )
        else:
            tip = (
                '请在 Altium Designer 里**打开该库 → File | Save**'
                '（只打开不保存不会产生快照），'
                '再点一次「读取库数据」即可自动转换。\n\n'
                'AD 保存后会在库目录的 History\\ 下生成明文快照；'
                '转换本身不需要 AD 常驻。\n\n'
                '⚠ 转明后请勿在 AD 里执行「Save All」——会把明文重新加密。'
            )
        try:
            from tkinter import messagebox
            messagebox.showwarning(
                '这些库需要转明',
                '以下库是 DLP 密文，本次没能转成明文：\n\n'
                '    %s\n\n'
                '%s\n\n'
                '%s'
                % (names, paths, tip),
                # parent 必须是已映射的 widget；self（Frame）在部分平台会报错，
                # 优先用外壳窗口，失败再退回 self
                parent=getattr(getattr(self, 'app', None), 'root', self))
        except Exception as e:
            # 🔴 不再静默：把提示也写进日志，保证用户一定看得到
            self._log('   ⚠ 以下库缺 History 快照，需在 AD 里 File|Open 打开后再试：')
            for r in blocked:
                self._log('       %s → %s' % (r.get('cat'), r.get('path')))
            self._log('   （提示窗异常：%r）' % e)

    def _warn_poll(self, msg):
        """AD 轮询没开时的友好提醒。"""
        try:
            from tkinter import messagebox
            messagebox.showwarning(
                '需要先打开 AD 轮询',
                '读取库数据需要通过正在运行的 Altium Designer。\n\n'
                '请先在 AD 里启动轮询脚本：\n'
                '    File → Run Script → Altium_API → Dispatcher.pas\n'
                '    → StartMCPServer → Run\n\n'
                '详情：\n%s' % msg, parent=self)
        except Exception:
            pass


# ------------------------------------------------------------------ 详情弹窗
def show_detail(root, title, data, extra=None):
    win = gs.popup(root, title, size='medium')
    head = ttk.Frame(win)
    head.pack(fill='x', padx=14, pady=(12, 6))
    ttk.Label(head, text=title, style='Title.TLabel').pack(side='left')

    body = tk.Frame(win, bg=PAL['border'])
    body.pack(fill='both', expand=True, padx=14, pady=(0, 8))
    inner = tk.Frame(body, bg=PAL['surface'])
    inner.pack(fill='both', expand=True, padx=1, pady=1)

    txt = tk.Text(inner, **gs.text_kw(), wrap='word')
    vs = ttk.Scrollbar(inner, orient='vertical', command=txt.yview)
    txt.configure(yscrollcommand=vs.set)
    txt.pack(side='left', fill='both', expand=True)
    vs.pack(side='right', fill='y')

    lines = []
    for k, v in (data or {}).items():
        if v:
            lines.append('%s：%s' % (k, v))
    if extra:
        lines.append('')
        lines.append('— 来源 —')
        for k, v in extra.items():
            lines.append('%s：%s' % (k, v))
    txt.insert('1.0', '\n'.join(lines) if lines else '（无参数）')
    txt.configure(state='disabled')

    bar = ttk.Frame(win)
    bar.pack(fill='x', padx=14, pady=(0, 12))
    ttk.Button(bar, text='关闭', style='Accent.TButton',
               command=win.destroy).pack(side='right')


# ------------------------------------------------------------------ 类别页
SCH_LABELS = [('name', '符号名'), ('料号', '料号'), ('MPN', 'MPN'),
              ('厂商', '厂商'), ('封装', '封装'), ('spec', 'spec'),
              ('Comment', 'Comment'), ('cls', '类别'), ('models', '关联模型')]

CLS_TEXT = {
    'normal': '物料条目（有料号）',
    'dclass': 'D 类物料 —— MPN-only 建档，料号按设计留空',
    'template': '模板 / 占位符号 —— 无参数通用图形，正常',
    'other': '其他（无料号、无 MPN）',
}


class CategoryPage(ttk.Frame):
    def __init__(self, parent, app, entry):
        super().__init__(parent)
        self.app = app
        self.entry = entry
        self._build()

    def _src_text(self, s):
        if not s:
            return '无明文来源（该库为密文库且无备份）'
        kind = {'current': '当前文件', 'history': 'History 备份',
                'backup': '明文备份'}.get(s['kind'], s['kind'])
        warn = '' if s['kind'] == 'current' else '  ⚠ 可能非最新'
        return '%s　%s　%s%s' % (kind, s['file'], s['mtime'], warn)

    def _build(self):
        e = self.entry

        head = tk.Frame(self, bg=PAL['bg'])
        head.pack(fill='x', padx=14, pady=(12, 0))
        ttk.Label(head, text='%s　%s' % (e['cat'], e['folder']),
                  style='Title.TLabel').pack(side='left')
        ttk.Button(head, text='重建缓存', style='Tool.TButton',
                   command=self.app.rebuild).pack(side='right')

        src = tk.Frame(self, bg=PAL['bg'])
        src.pack(fill='x', padx=14, pady=(4, 6))
        # 🔴 存引用 —— rebuild(reload_only=True) 后要更新这两个 Label 的时间
        #    （2026-10-15 之前没存，rebuild 后表格变了、时间还停在旧值）
        self.lbl_sch_src = ttk.Label(
            src, text='原理图库来源：' + self._src_text(e['sch_src']),
            style='Muted.TLabel')
        self.lbl_sch_src.pack(anchor='w')
        self.lbl_pcb_src = ttk.Label(
            src, text='PCB库来源：' + self._src_text(e['pcb_src']),
            style='Muted.TLabel')
        self.lbl_pcb_src.pack(anchor='w')

        pw = gs.paned(self, orient='v')
        pw.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        top = gs.card(pw, '原理图库 (SchLib)')
        pw.add(top, minsize=int(180 * gs.SCALE))
        self.sec_sch = Section(
            top, '符号（双击查看全部参数）',
            [('符号名', 'name', 150), ('料号', '料号', 130), ('MPN', 'MPN', 150),
             ('厂商', '厂商', 110), ('封装', '封装', 120), ('spec', 'spec', 260)],
            on_open=lambda d: show_detail(
                self, d.get('name', ''), self._sch_detail(d),
                {'原理图库': self._src_text(e['sch_src'])}))
        self.sec_sch.pack(fill='both', expand=True)
        self.sec_sch.set_rows(e['sch'])

        bot = gs.card(pw, 'PCB库 (PcbLib)')
        pw.add(bot, minsize=int(140 * gs.SCALE))
        self.sec_pcb = Section(
            bot, '封装（双击查看）',
            [('封装名', 'name', 320)],
            on_open=lambda d: show_detail(
                self, d.get('name', ''), {'封装名': d.get('name', '')},
                {'PCB库': self._src_text(e['pcb_src'])}))
        self.sec_pcb.pack(fill='both', expand=True)
        self.sec_pcb.set_rows(e['pcb'])

    def refresh(self):
        """从 self.entry 重新填表格与来源时间（rebuild(reload_only=True) 后调）。

        🔴 之前 rebuild 只 set_rows 了表格，「来源时间」两个 Label 没刷新，
           导致点完「读取库数据」时间也不变（2026-10-15 修）。
        """
        e = self.entry
        try:
            self.lbl_sch_src.config(
                text='原理图库来源：' + self._src_text(e['sch_src']))
            self.lbl_pcb_src.config(
                text='PCB库来源：' + self._src_text(e['pcb_src']))
        except Exception:
            pass
        self.sec_sch.set_rows(e['sch'])
        self.sec_pcb.set_rows(e['pcb'])

    @staticmethod
    def _sch_detail(d):
        out = {}
        for k, lab in SCH_LABELS:
            v = d.get(k, '')
            if k == 'cls':
                v = CLS_TEXT.get(v, v)
            elif k == 'models':
                if isinstance(v, list):
                    v = '、'.join(v) if v else ''
                if not v:
                    v = '（无关联模型）' if d.get('cls') != 'template' else ''
            if v:
                out[lab] = v
        # 有料号却没封装 → 明确指出
        if not d.get('封装') and d.get('cls') not in ('template', 'dclass'):
            out['⚠ 提示'] = '该符号未挂 PCB 封装（RECORD=45 模型链接缺失）'
        return out


# ------------------------------------------------------------------ 主应用
# ==================================================================== Schlib更新
class ScrollForm(ttk.Frame):
    """可纵向滚动的表单容器（内容多时不出界）。"""

    def __init__(self, parent):
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=PAL['surface'], highlightthickness=0,
                                bd=0)
        self.vs = ttk.Scrollbar(self, orient='vertical',
                                command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.vs.set)
        self.vs.pack(side='right', fill='y')
        self.canvas.pack(side='left', fill='both', expand=True)
        self.body = ttk.Frame(self.canvas)
        self._win = self.canvas.create_window((0, 0), window=self.body,
                                              anchor='nw')
        self.body.bind('<Configure>',
                       lambda _e: self.canvas.configure(
                           scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>',
                         lambda e: self.canvas.itemconfigure(self._win,
                                                             width=e.width))
        self.canvas.bind('<Enter>', lambda _e: self.canvas.bind_all(
            '<MouseWheel>', self._wheel))
        self.canvas.bind('<Leave>', lambda _e: self.canvas.unbind_all(
            '<MouseWheel>'))

    def _wheel(self, e):
        self.canvas.yview_scroll(int(-e.delta / 120), 'units')


class SchlibUpdatePage(ttk.Frame):
    """只更新原理图库、不动 PcbLib 的固化流程；参数由用户手动填写。"""

    TITLE = 'Schlib更新　—　只改原理图库，PcbLib 不动'
    SUBTITLE = (
        '两种模式：\n'
        '  · 带 symbol　—— 你提供源 SchLib 文件地址（或下载包目录），里面已有那颗新符号，直接取它；\n'
        '  · 不带 symbol —— 你指定一个同族物料符号作参考（默认就在目标库里），'
        '本工具复制它的图形、按 MPN 改名。\n'
        '流程：复制符号 → 按家规整改参数 → 链接封装（指向该类别 PcbLib 里已有的封装）'
        '→ 全项校验 → 通过才回写。\n'
        '护栏：Comment 可见、其余隐藏；Preferred 不设；封装链带 library_path；'
        '先写 temp 副本、校验不过绝不回写；回写前自动备份。参数一律由你手动填写。')

    PARAM_ROWS = [
        ('Comment', 'Comment（可见）'),
        ('Part Number', 'Part Number'),
        ('Specification', 'Specification'),
        ('MPN', 'MPN'),
        ('Manufacturer', 'Manufacturer'),
        ('Footprint', 'Footprint'),
    ]
    OPTIONS = [
        ('backup', '回写前备份正式库', True),
        ('write_back', '校验通过后回写正式库', True),
        ('purge_extra', '清除源符号的家规外参数', True),
        ('remove_existing_fp', '移除源符号已有封装链', True),
    ]

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._stop = None
        self._q = queue.Queue()
        self._busy = False
        self._src_params = None
        self.vars = {}
        self._loaded = False
        self._build()
        self._load_cfg()
        self.reload_categories()
        # MPN 变动时，不带 symbol 模式下的「新符号名」自动跟随
        self.vars['MPN'].trace_add('write', lambda *_: self._sync_new_name())
        self._mode_changed()

    # ------------------------------------------------------------ 布局
    def _build(self):
        head = tk.Frame(self, bg=PAL['bg'])
        head.pack(fill='x', padx=14, pady=(12, 2))
        ttk.Label(head, text=self.TITLE, style='Title.TLabel').pack(side='left')
        ttk.Button(head, text='打开程序目录', style='Tool.TButton',
                   command=self._open_dir).pack(side='right')

        tk.Label(self, bg=PAL['bg'], fg=PAL['muted'], justify='left',
                 font=gs.font(9), text=self.SUBTITLE
                 ).pack(anchor='w', padx=14, pady=(2, 6))

        pw = gs.paned(self, orient='h')
        pw.pack(fill='both', expand=True, padx=10, pady=(0, 10))

        left = gs.card(pw, '填写')
        pw.add(left, minsize=int(520 * gs.SCALE))
        self._build_form(left)

        right = gs.card(pw, '执行日志')
        pw.add(right, minsize=int(360 * gs.SCALE))
        self._build_log(right)

    def _build_form(self, parent):
        sf = ScrollForm(parent)
        sf.pack(fill='both', expand=True)
        f = sf.body
        self._form_body = f
        f.columnconfigure(1, weight=1)
        self._row = 0

        def section(text, host=None, col=0):
            host = host or f
            ttk.Label(host, text=text, style='Card.TLabel').grid(
                row=host._r, column=col, columnspan=2, sticky='w',
                padx=(0 if host is not f else 10, 0), pady=(10, 2))
            host._r += 1

        def row(label, widget, hint=None, host=None):
            host = host or f
            pad = (10, 6) if host is f else (0, 6)
            ttk.Label(host, text=label, style='Muted.TLabel').grid(
                row=host._r, column=0, sticky='w', padx=pad, pady=3)
            widget.grid(row=host._r, column=1, sticky='ew', padx=(0, 10), pady=3)
            host._r += 1
            if hint:
                ttk.Label(host, text=hint, style='Muted.TLabel',
                          font=gs.font(8)).grid(row=host._r, column=1,
                                                sticky='w', padx=(0, 10))
                host._r += 1

        f._r = 0

        # ① 模式
        section('① 模式')
        self._new('mode', 'with_symbol')
        mf = ttk.Frame(f)
        mf.grid(row=f._r, column=1, sticky='w', padx=(0, 10))
        f._r += 1
        for val, label, hint in (
                ('with_symbol', '带 symbol',
                 '源 SchLib 文件里已有一个新符号，直接取它'),
                ('ref_symbol', '不带 symbol',
                 '指定一个同族物料符号作参考，复制它的图形，输入 MPN 作新符号名')):
            line = ttk.Frame(mf)
            line.pack(anchor='w', fill='x')
            ttk.Radiobutton(line, text=label, value=val,
                            variable=self.vars['mode'],
                            command=self._mode_changed).pack(side='left')
            ttk.Label(line, text='— ' + hint, style='Muted.TLabel',
                      font=gs.font(8)).pack(side='left')

        # ② 源 / 参考（两块同格，按模式切换显示）
        self._blk_row = f._r
        f._r += 1
        self.blk_a = ttk.Frame(f)
        self.blk_b = ttk.Frame(f)
        self.blk_a.grid(row=self._blk_row, column=0, columnspan=2, sticky='ew')
        self.blk_b.grid(row=self._blk_row, column=0, columnspan=2, sticky='ew')
        self.blk_a._r = 0
        self.blk_b._r = 0
        self.blk_a.columnconfigure(1, weight=1)
        self.blk_b.columnconfigure(1, weight=1)
        self._build_blk_a(section, row)
        self._build_blk_b(section, row)
        f._r = self._blk_row + 1

        # ③ 目标库
        section('③ 目标库')
        self._new('category', '')
        self.cb_cat = ttk.Combobox(f, textvariable=self.vars['category'],
                                   state='readonly', font=gs.font(9))
        self.cb_cat.bind('<<ComboboxSelected>>', lambda *_: self._cat_changed())
        row('类别', self.cb_cat)
        self.lbl_paths = ttk.Label(f, text='', style='Muted.TLabel',
                                   font=gs.font(8), justify='left')
        self.lbl_paths.grid(row=f._r, column=1, sticky='w', padx=(0, 10))
        f._r += 1

        # ④ 参数
        section('④ 参数（手动填写）')
        for key, label in self.PARAM_ROWS:
            self._new(key, '')
            if key == 'Footprint':
                self.cb_fp = ttk.Combobox(f, textvariable=self.vars[key],
                                          font=gs.font(9))
                row(label + '（该类别 PcbLib 里的封装名）', self.cb_fp)
            else:
                row(label, self._entry(key))
        self._new('description', '')
        row('Description', self._entry('description'),
            '写入符号的 ComponentDescription（库浏览器显示的那行描述）')
        b3 = ttk.Frame(f)
        b3.grid(row=f._r, column=1, sticky='w', padx=(0, 10), pady=(6, 4))
        f._r += 1
        ttk.Button(b3, text='从源/参考符号带出', style='Tool.TButton',
                   command=self._fill_from_source).pack(side='left')
        ttk.Button(b3, text='清空参数', style='Tool.TButton',
                   command=self._clear_params).pack(side='left', padx=6)

        # ⑤ 选项
        section('⑤ 选项')
        opt = ttk.Frame(f)
        opt.grid(row=f._r, column=1, sticky='w', padx=(0, 10))
        f._r += 1
        for key, label, dflt in self.OPTIONS:
            self._new(key, dflt)
            ttk.Checkbutton(opt, text=label,
                            variable=self.vars[key]).pack(anchor='w')

        # ⑥ 执行
        act = ttk.Frame(f)
        act.grid(row=f._r, column=0, columnspan=2, sticky='w', padx=10,
                 pady=(12, 14))
        self.btn_run = ttk.Button(act, text='开始更新', style='Accent.TButton',
                                  command=self._run)
        self.btn_run.pack(side='left')
        self.btn_stop = ttk.Button(act, text='中止', style='Danger.TButton',
                                   command=self._abort, state='disabled')
        self.btn_stop.pack(side='left', padx=6)
        self.lbl_state = ttk.Label(act, text='就绪', style='Muted.TLabel')
        self.lbl_state.pack(side='left', padx=10)

    # ------------------------------------------------------- 模式两块
    def _build_blk_a(self, section, row):
        """带 symbol：源 SchLib + 符号名。"""
        h = self.blk_a
        section('② 源符号（带 symbol）', host=h)
        self._new('source_lib', '')
        self._new('symbol', '')
        row('源 SchLib', self._entry('source_lib', h), host=h,
            hint='可填 .SchLib 文件，也可直接给下载包目录（如 …\\ul_XXX\\Altium），会自动定位里面的 .SchLib')
        b = ttk.Frame(h)
        b.grid(row=h._r, column=1, sticky='w', padx=(0, 10), pady=(0, 4))
        h._r += 1
        ttk.Button(b, text='浏览…', style='Tool.TButton',
                   command=self._pick_source).pack(side='left')
        ttk.Button(b, text='浏览目录…', style='Tool.TButton',
                   command=lambda: self._pick_dir('source_lib')).pack(
            side='left', padx=6)
        ttk.Button(b, text='列出符号', style='Tool.TButton',
                   command=lambda: self._list_symbols('a')).pack(side='left')
        ttk.Button(b, text='读取参数', style='Tool.TButton',
                   command=lambda: self._read_params('a')).pack(side='left', padx=6)
        self.cb_symbol = ttk.Combobox(h, textvariable=self.vars['symbol'],
                                      font=gs.font(9))
        row('符号名', self.cb_symbol, host=h,
            hint='源库里的符号名（"列出符号"可带出下拉）')

    def _build_blk_b(self, section, row):
        """不带 symbol：同族参考库 + 参考符号 + 新符号名(=MPN)。"""
        h = self.blk_b
        section('② 同族参考（不带 symbol）', host=h)
        self._new('ref_lib', '')
        self._new('ref_symbol', '')
        self._new('new_name', '')
        row('参考 SchLib', self._entry('ref_lib', h), host=h,
            hint='默认就是目标类别库；也可给别的库或下载包目录（会自动定位 .SchLib）')
        b = ttk.Frame(h)
        b.grid(row=h._r, column=1, sticky='w', padx=(0, 10), pady=(0, 4))
        h._r += 1
        ttk.Button(b, text='浏览…', style='Tool.TButton',
                   command=self._pick_ref).pack(side='left')
        ttk.Button(b, text='浏览目录…', style='Tool.TButton',
                   command=lambda: self._pick_dir('ref_lib')).pack(
            side='left', padx=6)
        ttk.Button(b, text='同目标库', style='Tool.TButton',
                   command=self._ref_use_target).pack(side='left')
        ttk.Button(b, text='列出符号', style='Tool.TButton',
                   command=lambda: self._list_symbols('b')).pack(side='left', padx=6)
        ttk.Button(b, text='读取参数', style='Tool.TButton',
                   command=lambda: self._read_params('b')).pack(side='left')
        self.cb_ref = ttk.Combobox(h, textvariable=self.vars['ref_symbol'],
                                   font=gs.font(9))
        row('参考符号', self.cb_ref, host=h,
            hint='同族物料符号；新符号将复制它的图形')
        self._new('link_mpn', True)
        w = ttk.Frame(h)
        w.grid(row=h._r, column=1, sticky='ew', padx=(0, 10), pady=3)
        h._r += 1
        ttk.Entry(w, textvariable=self.vars['new_name'],
                  font=gs.font(9)).pack(side='left', fill='x', expand=True)
        ttk.Checkbutton(w, text='跟随 MPN', variable=self.vars['link_mpn']
                        ).pack(side='left', padx=6)
        ttk.Label(h, text='新符号名（= MPN，D 类规则 LibRef=MPN）',
                  style='Muted.TLabel', font=gs.font(8)).grid(
            row=h._r, column=1, sticky='w', padx=(0, 10))
        h._r += 1

    def _mode_changed(self):
        if 'mode' not in self.vars or 'blk_a' not in self.__dict__:
            return
        a = self.vars['mode'].get() == 'with_symbol'
        if a:
            self.blk_b.grid_remove()
            self.blk_a.grid()
        else:
            self.blk_a.grid_remove()
            self.blk_b.grid()
            if not self.vars['ref_lib'].get():
                self._ref_use_target()
        self._sync_new_name()
        self._save_cfg()

    def _ref_use_target(self):
        sch, _pcb = self._paths()
        if sch:
            self.vars['ref_lib'].set(sch)
            self._log('参考库已设为目标类别库：%s' % sch)

    def _sync_new_name(self):
        """不带 symbol 时，新符号名默认跟随 MPN。"""
        try:
            if self.vars.get('mode', tk.StringVar()).get() != 'ref_symbol':
                return
            if 'new_name' not in self.vars:
                return
            if self.vars.get('link_mpn') and self.vars['link_mpn'].get():
                self.vars['new_name'].set(self.vars['MPN'].get().strip())
        except Exception:
            pass


    def _build_log(self, parent):
        box = tk.Frame(parent, bg=PAL['border'])
        box.pack(fill='both', expand=True, padx=10, pady=10)
        inner = tk.Frame(box, bg=PAL['surface'])
        inner.pack(fill='both', expand=True, padx=1, pady=1)
        self.log = tk.Text(inner, **gs.text_kw(), wrap='word', state='disabled')
        vs = ttk.Scrollbar(inner, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=vs.set)
        self.log.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        for tag, color in (('info', PAL['text']), ('ok', PAL['ok']),
                           ('warn', '#B45309'), ('err', PAL['danger'])):
            self.log.tag_configure(tag, foreground=color)
        self.log.tag_configure('phase', foreground=PAL['accent'],
                               font=gs.font(9, bold=True))

    # ------------------------------------------------------------ 小工具
    def _new(self, key, default):
        if isinstance(default, bool):
            self.vars[key] = tk.BooleanVar(value=default)
        else:
            self.vars[key] = tk.StringVar(value=default)
        return self.vars[key]

    def _entry(self, key, parent=None):
        return ttk.Entry(parent or self._form_body,
                         textvariable=self.vars[key], font=gs.font(9))

    def _log(self, text, level='info'):
        self.log.configure(state='normal')
        self.log.insert('end', text + '\n', level)
        self.log.see('end')
        self.log.configure(state='disabled')

    def _clear_log(self):
        self.log.configure(state='normal')
        self.log.delete('1.0', 'end')
        self.log.configure(state='disabled')

    def _open_dir(self):
        try:
            os.startfile(HERE)
        except Exception as e:
            self._log('无法打开目录：%s' % e, 'warn')

    def _pick_source(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title='选择源 SchLib',
            filetypes=[('Altium 原理图库', '*.SchLib *.SCHLIB'),
                       ('所有文件', '*.*')])
        if p:
            self.vars['source_lib'].set(p)

    def _pick_ref(self):
        from tkinter import filedialog
        p = filedialog.askopenfilename(
            title='选择同族参考 SchLib',
            filetypes=[('Altium 原理图库', '*.SchLib *.SCHLIB'),
                       ('所有文件', '*.*')])
        if p:
            self.vars['ref_lib'].set(p)

    def _pick_dir(self, varkey):
        """选目录：自动找里面的 .SchLib（厂商下载包的常见用法）。"""
        from tkinter import filedialog
        d = filedialog.askdirectory(title='选择包含 .SchLib 的目录（如下载包的 Altium 目录）')
        if not d:
            return
        self.vars[varkey].set(d)
        what = '源 SchLib' if varkey == 'source_lib' else '参考 SchLib'
        self._try_resolve(varkey, what)

    # ------------------------------------------------------------ 数据
    def reload_categories(self):
        cats = [c['cat'] for c in self.app.data.get('categories', [])]
        self.cb_cat['values'] = cats
        if cats and not self.vars['category'].get():
            self.vars['category'].set(cats[0])
        self._cat_changed()

    def _cat_entry(self):
        name = self.vars['category'].get()
        for c in self.app.data.get('categories', []):
            if c['cat'] == name:
                return c
        return None

    def _paths(self):
        c = self._cat_entry()
        if not c:
            return None, None
        folder = os.path.join(self.app.data.get('lib_base', ''), c['folder'])
        return (os.path.join(folder, c['stem'] + '.SchLib'),
                os.path.join(folder, c['stem'] + '.PcbLib'))

    def _cat_changed(self):
        sch, pcb = self._paths()
        self.lbl_paths.config(
            text='SchLib：%s\nPcbLib：%s\n（PcbLib 仅作封装引用，不会被修改）'
                 % (sch, pcb))
        c = self._cat_entry()
        if c:
            self.cb_fp['values'] = [x['name'] for x in c.get('pcb', [])]
        # 「不带 symbol」模式下，参考库默认跟随目标类别库
        prev = getattr(self, '_last_target', None)
        cur = self.vars['ref_lib'].get().strip()
        if sch and (not cur or (prev and os.path.normcase(cur) ==
                                os.path.normcase(prev))):
            self.vars['ref_lib'].set(sch)
        self._last_target = sch
        self._save_cfg()

    # ------------------------------------------------------------ 动作
    def _src_of(self, which):
        """which='a' → (lib, sym, combobox)；'b' → 参考库/参考符号。"""
        if which == 'a':
            return (self.vars['source_lib'].get().strip(),
                    self.vars['symbol'].get().strip(), self.cb_symbol)
        return (self.vars['ref_lib'].get().strip(),
                self.vars['ref_symbol'].get().strip(), self.cb_ref)

    def _resolve_err(self, path, what):
        """路径能否解析成 .SchLib？返回错误串（'' 表示没问题）。"""
        try:
            import schlib_update as SU
            SU.resolve_lib_path(path, what)
            return ''
        except Exception as e:
            return str(e)

    def _try_resolve(self, varkey, what, quiet=False):
        """把（可能是目录的）路径解析成 .SchLib 并回填表单。返回 (ok, msg)。"""
        p = self.vars[varkey].get().strip()
        if not p:
            return True, ''
        try:
            import schlib_update as SU
            real, note = SU.resolve_lib_path(p, what)
        except Exception as e:
            if not quiet:
                self._log('✗ %s：%s' % (what, e), 'err')
            return False, str(e)
        if os.path.normcase(real) != os.path.normcase(p):
            self.vars[varkey].set(real)
            self._log('路径已解析 → %s' % real, 'info')
        return True, note

    def _list_symbols(self, which='a'):
        VARK = {'a': 'source_lib', 'b': 'ref_lib', 't': None}
        WHAT = {'a': '源 SchLib', 'b': '参考 SchLib', 't': '目标 SchLib'}
        varkey = VARK.get(which, 'source_lib')
        if varkey:
            # 先把（可能是目录的）路径解析并回填，再取值 —— 顺序反了会拿到旧值
            if not self._try_resolve(varkey, WHAT[which])[0]:
                return
        lib, _sym, cb = self._src_of(which)
        if not lib:
            self._log('请先选择%s' % WHAT[which], 'warn')
            return
        self._log('读取符号列表：%s' % lib)
        self.update_idletasks()
        try:
            import schlib_update as SU
            names = SU.list_symbols(lib)
            cb['values'] = names
            self._log('共 %d 个符号：%s%s' % (
                len(names), names[:12], ' …' if len(names) > 12 else ''), 'ok')
            if len(names) == 1:
                (self.vars['symbol'] if which == 'a'
                 else self.vars['ref_symbol']).set(names[0])
        except Exception as e:
            self._log('读取失败：%s' % e, 'err')

    def _read_params(self, which='a'):
        VARK = {'a': 'source_lib', 'b': 'ref_lib', 't': None}
        WHAT = {'a': '源 SchLib', 'b': '参考 SchLib', 't': '目标 SchLib'}
        varkey = VARK.get(which, 'source_lib')
        if varkey:
            # 解析/回填在前、取值在后（顺序反了会读错库）
            if not self._try_resolve(varkey, WHAT[which])[0]:
                return
        lib, sym, _cb = self._src_of(which)
        if not lib:
            self._log('请先填%s' % WHAT[which], 'warn')
            return
        if not sym:
            self._log('请先填%s的符号名' % WHAT[which], 'warn')
            return
        try:
            import schlib_update as SU
            p = SU.read_symbol_params(lib, sym)
            fps = []
            try:
                fps = SU.read_symbol_footprints(lib, sym)
            except Exception:
                pass
            self._src_params = p
            self._log('%s %s 现有参数：' % (WHAT[which], sym), 'ok')
            for k, v in p.items():
                self._log('    %-26s = %r' % (k, str(v)[:80]))
            if fps:
                self._log('    封装链: %s' % fps)
        except Exception as e:
            self._log('读取失败：%s' % e, 'err')
            self._log('   （读的是 %s：%s）' % (WHAT[which], lib), 'warn')

    def _fill_from_source(self):
        if not self._src_params:
            self._log('还没读到源/参考参数 —— 先点「读取参数」', 'warn')
            return
        n = 0
        for k, v in self._src_params.items():
            for key, _label in self.PARAM_ROWS:
                if key.lower() == k.lower():
                    self.vars[key].set(str(v))
                    n += 1
        self._log('已带出 %d 个参数到表单（请按家规修改后再执行）' % n, 'ok')

    def _clear_params(self):
        for key, _label in self.PARAM_ROWS:
            self.vars[key].set('')
        self.vars['description'].set('')
        self._log('参数已清空')

    def _collect(self):
        sch, pcb = self._paths()
        cfg = {
            'mode': self.vars['mode'].get(),
            'target_schlib': sch,
            'target_pcblib': pcb,
            'params': {k: self.vars[k].get().strip()
                       for k, _l in self.PARAM_ROWS},
            'description': self.vars['description'].get().strip(),
            'backup': bool(self.vars['backup'].get()),
            'write_back': bool(self.vars['write_back'].get()),
            'purge_extra': bool(self.vars['purge_extra'].get()),
            'remove_existing_fp': bool(self.vars['remove_existing_fp'].get()),
        }
        if cfg['mode'] == 'with_symbol':
            cfg['source_lib'] = self.vars['source_lib'].get().strip()
            cfg['symbol'] = self.vars['symbol'].get().strip()
            cfg['new_name'] = cfg['symbol']
        else:
            cfg['ref_lib'] = self.vars['ref_lib'].get().strip()
            cfg['ref_symbol'] = self.vars['ref_symbol'].get().strip()
            cfg['new_name'] = (self.vars['new_name'].get().strip()
                               or cfg['params'].get('MPN', '').strip())
        return cfg

    def _validate_form(self, cfg):
        errs = []
        if cfg['mode'] == 'with_symbol':
            if not cfg.get('source_lib'):
                errs.append('未选源 SchLib')
            else:
                e = self._resolve_err(cfg['source_lib'], '源 SchLib')
                if e:
                    errs.append(e)
            if not cfg.get('symbol'):
                errs.append('未填符号名')
        else:
            if not cfg.get('ref_lib'):
                errs.append('未选同族参考 SchLib')
            else:
                e = self._resolve_err(cfg['ref_lib'], '参考 SchLib')
                if e:
                    errs.append(e)
            if not cfg.get('ref_symbol'):
                errs.append('未填参考符号名')
            if not cfg.get('new_name'):
                errs.append('未填新符号名（= MPN）')
            elif cfg.get('new_name') == cfg.get('ref_symbol'):
                errs.append('新符号名与参考符号相同 —— 请填 MPN')
        if not cfg['target_schlib']:
            errs.append('未选目标类别')
        for k in ('MPN', 'Comment', 'Footprint'):
            if not cfg['params'].get(k, '').strip():
                errs.append('%s 不能为空' % k)
        if not cfg['description']:
            errs.append('Description 不能为空')
        if cfg['mode'] == 'ref_symbol' and cfg.get('new_name') \
                and cfg['params'].get('MPN') \
                and cfg['new_name'] != cfg['params']['MPN'].strip():
            errs.append('新符号名 %r 与 MPN %r 不一致（D 类规则 LibRef=MPN）'
                        % (cfg['new_name'], cfg['params']['MPN'].strip()))
        c = self._cat_entry()
        fp = cfg['params'].get('Footprint', '')
        if c and fp:
            names = [x['name'] for x in c.get('pcb', [])]
            if names and fp not in names:
                errs.append('封装 %r 不在 %s 的封装列表里（按查看器缓存口径）'
                            % (fp, c['cat']))
        return errs

    def _confirm(self, cfg):
        from tkinter import messagebox
        if cfg['mode'] == 'with_symbol':
            src = '源 SchLib：%s\n源符号：%s' % (cfg.get('source_lib'),
                                            cfg.get('symbol'))
        else:
            src = '同族参考：%s  →  %s' % (cfg.get('ref_lib'),
                                          cfg.get('ref_symbol'))
        return messagebox.askyesno(
            '确认执行',
            '%s\n模式：%s\n\n目标库：\n%s\n\n新符号名：%s\nMPN：%s\n'
            'Footprint：%s\n封装库（只读引用）：%s\n\n%s\n\n继续？'
            % (src,
               '带 symbol' if cfg['mode'] == 'with_symbol' else '不带 symbol（同族参考）',
               cfg['target_schlib'], cfg.get('new_name'),
               cfg['params'].get('MPN', ''), cfg['params'].get('Footprint', ''),
               cfg['target_pcblib'],
               '回写前会先备份正式库。' if cfg['backup']
               else '⚠ 未勾选备份 —— 回写将直接覆盖！'),
            parent=self)

    def _run(self):
        if self._busy:
            return
        # 先把（可能是下载包目录的）路径解析成 .SchLib 并回填，再收集配置
        mode = self.vars['mode'].get()
        if mode == 'with_symbol':
            self._try_resolve('source_lib', '源 SchLib')
        else:
            self._try_resolve('ref_lib', '参考 SchLib')
        cfg = self._collect()
        errs = self._validate_form(cfg)
        if errs:
            self._clear_log()
            for e in errs:
                self._log('✗ %s' % e, 'err')
            self._log('请修正后再执行。', 'warn')
            return
        if not self._confirm(cfg):
            self._log('已取消')
            return

        self._clear_log()
        self._log('模式：%s' % ('带 symbol' if cfg['mode'] == 'with_symbol'
                              else '不带 symbol（同族参考）'))
        self._log('开始：%s  ←  %s' % (cfg['target_schlib'], cfg.get('new_name')))
        self._busy = True
        self._stop = threading.Event()
        self.btn_run.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.lbl_state.config(text='执行中…')
        self._save_cfg()

        import schlib_update as SU   # noqa: F401

        def worker():
            try:
                res = self._run_pipeline(
                    cfg, lambda t, lv='info': self._q.put((t, lv)),
                    should_stop=self._stop.is_set)
            except Exception as e:
                res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
            self._q.put(('__done__', res))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._drain)

    def _run_pipeline(self, cfg, log, should_stop):
        """子类覆写以换用别的流水线。"""
        import schlib_update as SU
        return SU.run_update(cfg, log, should_stop)

    def _abort(self):
        if self._stop:
            self._stop.set()
            self._log('已请求中止（当前步骤结束后停止）', 'warn')

    def _drain(self):
        try:
            while True:
                text, lv = self._q.get_nowait()
                if text == '__done__':
                    self._finish(lv)
                    return
                self._log(text, lv)
        except queue.Empty:
            pass
        if self._busy:
            self.after(80, self._drain)

    def _finish(self, res):
        self._busy = False
        self.btn_run.config(state='normal')
        self.btn_stop.config(state='disabled')
        self._log('')
        if res.get('ok'):
            self.lbl_state.config(text='完成')
            w = res.get('summary', {}).get('written')
            self._log('✅ 成功。%s' % ('已回写正式库。' if w else '未回写正式库。'),
                      'ok')
            if w:
                self._log('提示：可到「重建缓存」刷新查看器数据。', 'info')
        else:
            self.lbl_state.config(text='失败')
            self._log('❌ 失败：%s' % res.get('error'), 'err')
            self._log('正式库未被修改。', 'warn')

    # ------------------------------------------------------------ 配置持久化
    def _cfg_path(self):
        return os.path.join(HERE, 'schlib_update_cfg.json')

    def _save_cfg(self):
        try:
            gs.save_json_cfg(self._cfg_path(),
                             {k: v.get() for k, v in self.vars.items()})
        except Exception:
            pass

    def _load_cfg(self):
        try:
            d = gs.load_json_cfg(self._cfg_path(), {}) or {}
        except Exception:
            d = {}
        for k, v in d.items():
            if k in self.vars:
                try:
                    self.vars[k].set(v)
                except Exception:
                    pass
        self._cat_changed()
        self._mode_changed()
        self._loaded = True


class SymbolUpdatePage(SchlibUpdatePage):
    """对**已建库物料**换 symbol 图形（默认保留其参数/描述/封装链）。

    参考件由用户提供 SchLib（可给下载包目录）。设计上「只换图形」，
    参数与封装链原样保留；想顺手改的话，取消对应勾选即可开放表单。
    """

    TITLE = 'symbol更新　—　给已建库物料换原理图符号'
    SUBTITLE = (
        '用途：库里某个物料条目的**图形过时/画错了**，用厂商下载包（或别的库）里同料的符号替换它，'
        '而**保留该物料的身份**（名字 / 参数 / 封装链）。\n'
        '做法：图形取自【参考件】，名字·描述·参数·封装链取自【目标件】；'
        '换完逐项校验（图形=参考件、其余=目标件、符号数不变），通过才回写。\n'
        '参考 SchLib 可填文件，也可直接给下载包目录（会自动定位里面的 .SchLib）。'
        'PcbLib 全程不动。')

    def _build_form(self, parent):
        sf = ScrollForm(parent)
        sf.pack(fill='both', expand=True)
        f = sf.body
        self._form_body = f
        f.columnconfigure(1, weight=1)
        f._r = 0

        def section(text, host=None):
            host = host or f
            ttk.Label(host, text=text, style='Card.TLabel').grid(
                row=host._r, column=0, columnspan=2, sticky='w',
                padx=(0 if host is not f else 10, 0), pady=(10, 2))
            host._r += 1

        def row(label, widget, hint=None, host=None):
            host = host or f
            pad = (10, 6) if host is f else (0, 6)
            ttk.Label(host, text=label, style='Muted.TLabel').grid(
                row=host._r, column=0, sticky='w', padx=pad, pady=3)
            widget.grid(row=host._r, column=1, sticky='ew', padx=(0, 10), pady=3)
            host._r += 1
            if hint:
                ttk.Label(host, text=hint, style='Muted.TLabel',
                          font=gs.font(8)).grid(row=host._r, column=1,
                                                sticky='w', padx=(0, 10))
                host._r += 1

        # ① 目标
        section('① 目标（要换图的物料）')
        self._new('category', '')
        self.cb_cat = ttk.Combobox(f, textvariable=self.vars['category'],
                                   state='readonly', font=gs.font(9))
        self.cb_cat.bind('<<ComboboxSelected>>', lambda *_: self._cat_changed())
        row('类别', self.cb_cat)
        self.lbl_paths = ttk.Label(f, text='', style='Muted.TLabel',
                                   font=gs.font(8), justify='left')
        self.lbl_paths.grid(row=f._r, column=1, sticky='w', padx=(0, 10))
        f._r += 1
        self._new('target_symbol', '')
        self.cb_target = ttk.Combobox(f, textvariable=self.vars['target_symbol'],
                                      font=gs.font(9))
        self.cb_target.bind('<KeyRelease>', self._on_target_key)
        self.cb_target.bind('<<ComboboxSelected>>',
                            lambda *_: self._on_target_picked())
        self.cb_target.bind('<FocusIn>', self._on_target_focus)
        row('目标 MPN', self.cb_target,
            '输入关键词即筛选（型号片段 / 料号都行），再从下拉里选 —— 换类别自动刷新')
        b1 = ttk.Frame(f)
        b1.grid(row=f._r, column=1, sticky='w', padx=(0, 10), pady=(0, 4))
        f._r += 1
        ttk.Button(b1, text='刷新候选', style='Tool.TButton',
                   command=lambda: self._list_symbols('t')).pack(side='left')
        ttk.Button(b1, text='读取参数', style='Tool.TButton',
                   command=lambda: self._read_params('t')).pack(side='left', padx=6)

        # ② 参考
        section('② 参考（图形从这里来）')
        self._new('ref_lib', '')
        row('参考 SchLib', self._entry('ref_lib'),
            hint='可填 .SchLib 文件，也可直接给下载包目录（如 …\\ul_XXX\\Altium）')
        b2 = ttk.Frame(f)
        b2.grid(row=f._r, column=1, sticky='w', padx=(0, 10), pady=(0, 4))
        f._r += 1
        ttk.Button(b2, text='浏览…', style='Tool.TButton',
                   command=self._pick_ref).pack(side='left')
        ttk.Button(b2, text='浏览目录…', style='Tool.TButton',
                   command=lambda: self._pick_dir('ref_lib')).pack(side='left', padx=6)
        ttk.Button(b2, text='列出符号', style='Tool.TButton',
                   command=lambda: self._list_symbols('b')).pack(side='left')
        ttk.Button(b2, text='读取参数', style='Tool.TButton',
                   command=lambda: self._read_params('b')).pack(side='left', padx=6)
        self._new('ref_symbol', '')
        self.cb_ref = ttk.Combobox(f, textvariable=self.vars['ref_symbol'],
                                   font=gs.font(9))
        row('参考符号', self.cb_ref, '新图形取自这个符号')

        # ③ 保留项
        section('③ 保留项（默认全保留 = 只换图形）')
        self._edit_widgets = []
        opt = ttk.Frame(f)
        opt.grid(row=f._r, column=1, sticky='w', padx=(0, 10))
        f._r += 1
        for key, label, dflt in (
                ('keep_params', '保留目标件参数', True),
                ('keep_description', '保留目标件描述', True),
                ('keep_footprints', '保留目标件封装链', True),
                ('backup', '回写前备份正式库', True),
                ('write_back', '校验通过后回写正式库', True)):
            self._new(key, dflt)
            ttk.Checkbutton(opt, text=label, variable=self.vars[key],
                            command=self._toggle_edit).pack(anchor='w')

        pk = ttk.Frame(f)
        pk.grid(row=f._r, column=0, columnspan=2, sticky='w', padx=10, pady=(6, 0))
        f._r += 1
        ttk.Label(pk, text='参数（取消「保留参数」后才可改）',
                  style='Muted.TLabel', font=gs.font(8)).pack(anchor='w')
        for key, label in self.PARAM_ROWS:
            self._new(key, '')
            w = self._entry(key) if key != 'Footprint' else \
                ttk.Combobox(f, textvariable=self.vars[key], font=gs.font(9))
            if key == 'Footprint':
                self.cb_fp = w
            row(label, w)
            self._edit_widgets.append(w)
        self._new('description', '')
        de = self._entry('description')
        row('Description', de, '取消「保留描述」后才可改')
        self._edit_widgets.append(de)
        b3 = ttk.Frame(f)
        b3.grid(row=f._r, column=1, sticky='w', padx=(0, 10), pady=(6, 4))
        f._r += 1
        ttk.Button(b3, text='从目标件带出', style='Tool.TButton',
                   command=self._fill_from_target).pack(side='left')
        ttk.Button(b3, text='清空参数', style='Tool.TButton',
                   command=self._clear_params).pack(side='left', padx=6)

        # ④ 执行
        act = ttk.Frame(f)
        act.grid(row=f._r, column=0, columnspan=2, sticky='w', padx=10,
                 pady=(12, 14))
        self.btn_run = ttk.Button(act, text='开始换图', style='Accent.TButton',
                                  command=self._run)
        self.btn_run.pack(side='left')
        self.btn_stop = ttk.Button(act, text='中止', style='Danger.TButton',
                                   command=self._abort, state='disabled')
        self.btn_stop.pack(side='left', padx=6)
        self.lbl_state = ttk.Label(act, text='就绪', style='Muted.TLabel')
        self.lbl_state.pack(side='left', padx=10)
        self._toggle_edit()

    # ------------------------------------------------------- 联动
    def _toggle_edit(self):
        """按「保留」勾选决定参数/描述控件是否可编辑。"""
        pe = 'disabled' if self.vars['keep_params'].get() else 'normal'
        for w in self._edit_widgets:
            if w is self._edit_widgets[-1]:
                continue
            try:
                w.configure(state=pe)
            except Exception:
                pass
        try:
            self._edit_widgets[-1].configure(
                state='disabled' if self.vars['keep_description'].get()
                else 'normal')
        except Exception:
            pass
        try:
            self.cb_fp.configure(
                state='disabled' if self.vars['keep_footprints'].get()
                else 'normal')
        except Exception:
            pass
        if getattr(self, '_loaded', False):     # 构建期不落盘，免得覆盖已存配置
            self._save_cfg()

    def _cat_changed(self):
        super()._cat_changed()
        c = self._cat_entry()
        if c:
            self.cb_fp['values'] = [x['name'] for x in c.get('pcb', [])]
        # 换类别后目标候选列表作废 → 立即按新类别重建（读缓存，瞬时）
        self._tmap = {}
        self._target_all = []
        self.cb_target['values'] = []
        if self._cat_entry():
            self._list_targets()

    # ------------------------------------------------------- 列出/读取
    def _target_name(self):
        """把下拉里选的「MPN 显示串」还原成真正的符号名。"""
        s = self.vars['target_symbol'].get().strip()
        m = getattr(self, '_tmap', None) or {}
        if s in m:
            return m[s]
        if s.endswith(')') and '  (' in s:          # 兜底：从 'MPN  (name)' 里取
            return s.rsplit('  (', 1)[1][:-1]
        return s

    def _on_target_key(self, ev=None):
        """输入即筛选：把候选收敛到「含所输关键词」的项，并尝试展开下拉。"""
        if ev is not None and ev.keysym in ('Up', 'Down', 'Return', 'Escape',
                                            'Tab'):
            return          # 方向键/回车/退出交给下拉自己处理
        self._filter_targets(self.vars['target_symbol'].get().strip())

    def _filter_targets(self, typed):
        allv = getattr(self, '_target_all', None) or []
        if not allv:
            return
        kw = typed.upper()
        hits = list(allv) if not typed else [d for d in allv if kw in d.upper()]
        self.cb_target['values'] = hits
        if typed and hits:
            try:
                self.cb_target.event_generate('<Down>')   # 展开看筛选结果
            except Exception:
                pass
        elif typed and not hits:
            self._log('没有匹配 %r 的候选（清空输入可看全部 %d 项）'
                      % (typed, len(allv)), 'warn')

    def _on_target_picked(self):
        self.cb_target['values'] = list(getattr(self, '_target_all', []) or [])

    def _on_target_focus(self, _ev=None):
        # 聚焦即全选：选过一次后想换个词搜，直接输入就替换掉，不用先删
        try:
            self.cb_target.selection_range(0, 'end')
        except Exception:
            pass

    def _list_targets(self):
        """目标候选：**按 MPN 显示**（取自查看器缓存；缓存缺该类别时退回直接列库）。"""
        c = self._cat_entry()
        rows = (c or {}).get('sch') or []
        if not rows:
            lib, _s, _cb = self._src_of('t')
            try:
                import schlib_update as SU
                names = SU.list_symbols(lib)
            except Exception as e:
                self._log('读取失败：%s' % e, 'err')
                return
            self._tmap = {n: n for n in names}
            self._target_all = sorted(names)
            self.cb_target['values'] = list(self._target_all)
            self._log('缓存无该类别，直接列库：%d 个符号' % len(names), 'ok')
            return

        disp, self._tmap = [], {}
        for r in rows:
            n = r['name']
            mpn = (r.get('MPN') or '').strip()
            d = n if (not mpn or mpn == n) else '%s  (%s)' % (mpn, n)
            self._tmap[d] = n
            disp.append(d)
        disp.sort(key=str.upper)      # 显示串以 MPN 打头 → 按显示串排序最自洽
        self._target_all = list(disp)
        self.cb_target['values'] = list(disp)
        n_mpn = sum(1 for r in rows if (r.get('MPN') or '').strip()
                    and (r.get('MPN') or '').strip() != r['name'])
        self._log('目标候选 %d 个（其中 %d 个显示 MPN）' % (len(disp), n_mpn), 'ok')
        # 上次留下的值若已不在候选里（改过名 / 换过类别），直接清掉，免得白跑一次
        cur = self.vars['target_symbol'].get().strip()
        if cur and cur not in self._tmap:
            self.vars['target_symbol'].set('')
            self._log('原选项 %r 已不在候选里，已清空 —— 请从下拉重选' % cur,
                      'warn')

    def _list_symbols(self, which='a'):
        if which == 't':
            self._list_targets()
            return
        return super()._list_symbols(which)

    def _src_of(self, which):
        if which == 't':
            sch, _pcb = self._paths()
            return (sch or '', self._target_name(), self.cb_target)
        return super()._src_of(which)

    def _fill_from_target(self):
        return self._fill_from_source()

    # ------------------------------------------------------- 收集/校验
    def _collect(self):
        sch, pcb = self._paths()
        return {
            'target_schlib': sch,
            'target_pcblib': pcb,
            'target_symbol': self._target_name(),
            'target_display': self.vars['target_symbol'].get().strip(),
            'ref_lib': self.vars['ref_lib'].get().strip(),
            'ref_symbol': self.vars['ref_symbol'].get().strip(),
            'keep_params': bool(self.vars['keep_params'].get()),
            'keep_description': bool(self.vars['keep_description'].get()),
            'keep_footprints': bool(self.vars['keep_footprints'].get()),
            'params': {k: self.vars[k].get().strip()
                       for k, _l in self.PARAM_ROWS},
            'description': self.vars['description'].get().strip(),
            'footprint': self.vars['Footprint'].get().strip(),
            'backup': bool(self.vars['backup'].get()),
            'write_back': bool(self.vars['write_back'].get()),
        }

    def _validate_form(self, cfg):
        errs = []
        if not cfg['target_schlib']:
            errs.append('未选目标类别')
        if not cfg['target_symbol']:
            errs.append('未选目标 MPN（输入关键词后从下拉里选）')
        elif self.vars['target_symbol'].get().strip() not in self._tmap:
            errs.append('目标 MPN %r 不是候选里的项 —— 请从下拉里选定'
                        % self.vars['target_symbol'].get().strip())
        if not cfg['ref_lib']:
            errs.append('未选参考 SchLib')
        else:
            e = self._resolve_err(cfg['ref_lib'], '参考 SchLib')
            if e:
                errs.append(e)
        if not cfg['ref_symbol']:
            errs.append('未填参考符号名')
        elif cfg['ref_symbol'] == cfg['target_symbol']:
            errs.append('参考符号与目标项同名 —— 那样没有意义')
        if not cfg['keep_params']:
            for k in ('MPN', 'Comment', 'Footprint'):
                if not cfg['params'].get(k):
                    errs.append('%s 不能为空（已取消保留参数）' % k)
        if not cfg['keep_description'] and not cfg['description']:
            errs.append('Description 不能为空（已取消保留描述）')
        if not cfg['keep_footprints'] and not cfg['footprint']:
            errs.append('未填新封装名（已取消保留封装链）')
        return errs

    def _confirm(self, cfg):
        from tkinter import messagebox
        keep = []
        if cfg['keep_params']:
            keep.append('参数')
        if cfg['keep_description']:
            keep.append('描述')
        if cfg['keep_footprints']:
            keep.append('封装链')
        return messagebox.askyesno(
            '确认换图',
            '目标库：%s\n目标符号：%s\n参考图形：%s  →  %s\n\n'
            '保留：%s\n（图形取自参考件；符号数不变；PcbLib 不动）\n\n%s\n\n继续？'
            % (cfg['target_schlib'], cfg['target_symbol'], cfg['ref_lib'],
               cfg['ref_symbol'], '、'.join(keep) or '（无 —— 全部用参考件/表单）',
               '回写前会先备份正式库。' if cfg['backup']
               else '⚠ 未勾选备份 —— 回写将直接覆盖！'),
            parent=self)

    def _run(self):
        if self._busy:
            return
        self._try_resolve('ref_lib', '参考 SchLib')
        cfg = self._collect()
        errs = self._validate_form(cfg)
        if errs:
            self._clear_log()
            for e in errs:
                self._log('✗ %s' % e, 'err')
            self._log('请修正后再执行。', 'warn')
            return
        if not self._confirm(cfg):
            self._log('已取消')
            return

        self._clear_log()
        self._log('模式：换图形（保留 %s）'
                  % ('、'.join(k for k, v in
                              (('参数', cfg['keep_params']),
                               ('描述', cfg['keep_description']),
                               ('封装链', cfg['keep_footprints'])) if v) or '无'))
        self._log('目标：%s  ←  图形取自 %s'
                  % (cfg['target_symbol'], cfg['ref_symbol']))
        self._busy = True
        self._stop = threading.Event()
        self.btn_run.config(state='disabled')
        self.btn_stop.config(state='normal')
        self.lbl_state.config(text='执行中…')
        self._save_cfg()

        def worker():
            try:
                res = self._run_pipeline(
                    cfg, lambda t, lv='info': self._q.put((t, lv)),
                    should_stop=self._stop.is_set)
            except Exception as e:
                res = {'ok': False, 'error': '%s: %s' % (type(e).__name__, e)}
            self._q.put(('__done__', res))

        threading.Thread(target=worker, daemon=True).start()
        self.after(80, self._drain)

    def _run_pipeline(self, cfg, log, should_stop):
        import schlib_update as SU
        return SU.run_symbol_update(cfg, log, should_stop)

    def _cfg_path(self):
        return os.path.join(HERE, 'symbol_update_cfg.json')

    def _load_cfg(self):
        super()._load_cfg()
        self._toggle_edit()


class App:
    def __init__(self):
        self.root = gs.init('元件库查看器', size=(1360, 880),
                            appid=APP_ID)
        # ⚠️ AUMID 必须在 Tk 建窗之后再设一次：
        #   gs.init() 内部会 _set_taskbar_appid('WorkBuddy.<title>')，
        #   若在 init 前调本函数，会被它覆盖掉，
        #   任务栏就按错的 AUMID 找不到图标、回落 Python 图标。
        set_app_user_model_id()
        self.icon_state = apply_icon(self.root)
        style_tree()
        self.data = self._load()
        self._build()
        self.root.mainloop()

    def _load(self):
        if not os.path.exists(CACHE):
            return {'built_at': '(未生成)', 'categories': []}
        with open(CACHE, encoding='utf-8') as f:
            return json.load(f)

    def _build(self):
        nav = gs.sidebar(self.root)
        nav.pack(fill='both', expand=True)
        self.nav = nav
        self.pages = {}

        # 首页：物料库目录总览 + 「读取库数据」
        self.home_page = HomePage(nav.body, self)
        nav.add(self.home_page, text='Home', icon='home', group='常用')

        cats = {c['cat']: c for c in self.data['categories']}
        # ORDER 里有的按 ORDER 排；ORDER 外的自动追加（保持 lib_reader 顺序）
        seq = [c for c in ORDER if c in cats] +               [c for c in cats if c not in ORDER]
        for cat in seq:
            e = cats.get(cat)
            if not e:
                continue
            group, icon = META.get(cat, ('其他', 'file'))
            page = CategoryPage(nav.body, self, e)
            nav.add(page, text=cat, icon=icon, group=group)
            self.pages[cat] = page

        # 工具页：只更新 SchLib、不动 PcbLib
        self.update_page = SchlibUpdatePage(nav.body, self)
        nav.add(self.update_page, text='Schlib更新', icon='save', group='工具')
        # 工具页：给已建库物料换 symbol 图形
        self.symbol_page = SymbolUpdatePage(nav.body, self)
        nav.add(self.symbol_page, text='symbol更新', icon='battery', group='工具')
        # 工具页：出栈/入栈式删除（原理图封装 / PCB封装 两个子 tab）
        # 🔴 delete_page 内所有触及 olefile 的动作都丢给外部进程
        #    （_del_worker.py）—— 本 GUI 跑在系统 312，有 tkinter 但无 olefile。
        self.delete_page = DeletePage(nav.body, self)
        nav.add(self.delete_page, text='删除', icon='trash', group='工具')

        # 底部状态栏
        bar = tk.Frame(self.root, bg=PAL['menu_bg'],
                       highlightthickness=1, highlightbackground=PAL['border'])
        bar.place(relx=0, rely=1, anchor='sw', relwidth=1)
        n_s = sum(len(c['sch']) for c in self.data['categories'])
        n_p = sum(len(c['pcb']) for c in self.data['categories'])
        self.status = ttk.Label(
            bar, style='Status.TLabel',
            text='缓存 %s　|　类别 %d　符号 %d　封装 %d'
                 % (self.data.get('built_at', '-'),
                    len(self.data['categories']), n_s, n_p))
        self.status.pack(side='left', padx=12, pady=3)

    def rebuild(self, reload_only=False):
        """重建缓存并重载界面。

        reload_only=True 时只从磁盘重读 lib_cache.json 刷新各页，
        不再次触发构建（供 Home 页「读取库数据」完成后调用）。
        """
        if reload_only:
            self.data = self._load()
            cats = {c['cat']: c for c in self.data['categories']}
            for cat, page in self.pages.items():
                e = cats.get(cat)
                if not e:
                    continue
                page.entry = e
                if hasattr(page, 'refresh'):
                    page.refresh()          # 🔴 连「来源时间」Label 一起刷
                else:
                    page.sec_sch.set_rows(e['sch'])
                    page.sec_pcb.set_rows(e['pcb'])
            if getattr(self, 'home_page', None) is not None:
                try:
                    self.home_page.refresh()
                except Exception:
                    pass
            # 🔴 其余工具页（删除 / Schlib更新 / symbol更新）也必须刷新，
            #    否则它们的候选栈、类别下拉框会停在旧快照。
            self._refresh_aux_pages()
            n_s = sum(len(c['sch']) for c in self.data['categories'])
            n_p = sum(len(c['pcb']) for c in self.data['categories'])
            self.status.config(text='缓存 %s　|　类别 %d　符号 %d　封装 %d'
                                    % (self.data.get('built_at', '-'),
                                       len(self.data['categories']), n_s, n_p))
            return
        self.status.config(text='正在重建缓存…')
        self.root.update_idletasks()
        try:
            _rc, _out, _err = wb.run('build', timeout=300)
            p = type('X', (), {'returncode': _rc, 'stdout': _out,
                               'stderr': _err})()
            ok = (_rc == 0)
        except Exception as ex:
            ok = False
            p = type('X', (), {'stdout': str(ex), 'stderr': '', 'returncode': -1})()
        if not ok:
            self.status.config(text='重建失败：%s' % (p.stderr or p.stdout or '')[:120])
            return
        self.data = self._load()
        cats = {c['cat']: c for c in self.data['categories']}
        for cat, page in self.pages.items():
            e = cats.get(cat)
            if not e:
                continue
            page.entry = e
            page.sec_sch.set_rows(e['sch'])
            page.sec_pcb.set_rows(e['pcb'])
        n_s = sum(len(c['sch']) for c in self.data['categories'])
        n_p = sum(len(c['pcb']) for c in self.data['categories'])
        self.status.config(text='已重建 %s　|　类别 %d　符号 %d　封装 %d'
                                % (self.data.get('built_at', '-'),
                                   len(self.data['categories']), n_s, n_p))
        self._refresh_aux_pages()

    def _refresh_aux_pages(self):
        """刷新「删除 / Schlib更新 / symbol更新」三页的数据源。

        由 rebuild(reload_only=True)（Home 页「读取库数据」完成后调用）
        以及全量 rebuild 末尾调用。每页各自 try/except，单页失败不影响其它页。
        """
        # 删除页：重新问一遍外部进程（GUI 无 olefile），并保留已入栈清单
        dp = getattr(self, 'delete_page', None)
        if dp is not None:
            try:
                dp.reload(keep_stack=True)
            except Exception:
                pass
        # 两个更新页：重填类别下拉框（数据来自 self.data，已在上方刷新）
        for attr in ('update_page', 'symbol_page'):
            pg = getattr(self, attr, None)
            if pg is not None and hasattr(pg, 'reload_categories'):
                try:
                    pg.reload_categories()
                except Exception:
                    pass


def selftest():
    """诊断模式：在**当前进程**里跑一遍 on_fetch 的核心步骤，结果落盘。

    用法：`元件库查看器.exe --selftest [输出文件]`
    🔴 故意**不重定向** stdout —— 就是要暴露 windowed exe 下
       `sys.stdout is None` 引起的写入错误。
    """
    out = (sys.argv[2] if len(sys.argv) > 2
           else os.path.join(HERE, 'selftest.log'))
    lines = []

    def L(s):
        lines.append(str(s))

    L('frozen        = %s' % getattr(sys, 'frozen', False))
    L('sys.executable= %s' % sys.executable)
    L('HERE          = %s' % HERE)
    L('RES           = %s' % RES)
    L('sys.stdout    = %r' % (sys.stdout,))
    L('sys.stderr    = %r' % (sys.stderr,))
    L('')
    L('--- 试写 stdout ---')
    try:
        print('selftest hello')
        L('print(stdout): OK')
    except Exception as e:
        L('print(stdout): FAIL %r' % (e,))
    L('')
    L('--- plainize ---')
    try:
        import plainize
        L('import plainize OK')
        L('get_base() = %s' % plainize.get_base())
        try:
            rep = plainize.plainize_all(log=L)
            L('plainize_all OK: converted=%d already=%d no_snapshot=%d '
              'failed=%d missing=%d'
              % (len(rep['converted']), len(rep['already']),
                 len(rep['no_snapshot']), len(rep['failed']),
                 len(rep['missing'])))
            for r in rep['converted']:
                L('  converted: %s' % r['detail'])
            for r in rep['no_snapshot']:
                L('  no_snapshot: %s' % r['path'])
            for r in rep['failed']:
                L('  failed: %s / %s' % (r['path'], r['detail']))
        except Exception:
            L('plainize_all FAIL:\n' + traceback.format_exc())
    except Exception:
        L('import plainize FAIL:\n' + traceback.format_exc())
    L('')
    L('--- 合成快照转明测试（不碰真库）---')
    try:
        import shutil
        import tempfile
        import zipfile
        td = os.path.join(tempfile.gettempdir(), 'lv_plainize_probe')
        try:
            shutil.rmtree(td)
        except Exception:
            pass
        catdir = os.path.join(td, 'cat')
        os.makedirs(os.path.join(catdir, 'History'), exist_ok=True)
        # ① “密文正本” ← 用真实密文库的字节
        dense = os.path.join(plainize.get_base(), 'capacitor',
                             'Capacitor.PcbLib')
        plain_src = os.path.join(plainize.get_base(), 'resistor',
                                'Resistor.PcbLib')
        probe = os.path.join(catdir, 'Probe.PcbLib')
        with open(dense, 'rb') as fi, open(probe, 'wb') as fo:
            fo.write(fi.read())
        L('  合成正本 kind=%s (%d B)' % (plainize.kind(probe),
                                  os.path.getsize(probe)))
        # ② “明文快照” ← 用另一个明文库打成 zip
        z = os.path.join(catdir, 'History', 'Probe.~(1).PcbLib.Zip')
        with open(plain_src, 'rb') as f:
            pdata = f.read()
        with zipfile.ZipFile(z, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('Probe.~(1).PcbLib', pdata)
        L('  合成快照 kind=%s (%d B)' % (plainize.kind(z),
                                  os.path.getsize(z)))
        L('  _find_snapshots -> %d 份'
          % len(plainize._find_snapshots(catdir, 'Probe', '.pcblib')))
        # ③ 跑转明
        r = plainize.plainize_one('测试', 'cat', 'Probe', base=td, log=L,
                                  ext='.PcbLib')
        L('  action=%s' % r.get('action'))
        L('  detail=%s' % r.get('detail'))
        L('  正本 kind 现在 = %s' % plainize.kind(probe))
        L('  >>> 结论: %s'
          % ('✅ exe 能转' if plainize.kind(probe) == 'plain'
             else '❌ exe 转不了'))
        try:
            shutil.rmtree(td)
        except Exception:
            pass
    except Exception:
        L('  合成测试异常:\n' + traceback.format_exc())
    L('')
    L('--- build ---')
    try:
        rc, o, e = wb.run('build', timeout=600)
        L('wb.run(build) rc=%s out=%d B err=%d B' % (rc, len(o), len(e)))
        for ln in (o or '').splitlines()[-12:]:
            L('  | ' + ln)
        if e:
            L('  ERR: ' + e[-400:])
    except Exception:
        L('wb.run FAIL:\n' + traceback.format_exc())
    try:
        with open(out, 'w', encoding='utf-8', errors='replace') as f:
            f.write('\n'.join(lines))
        return 0
    except Exception:
        return 1


def main():
    # 🔴🔴 打包态：exe 除了当 GUI，还要能当 worker 用。
    #   因为 windowed exe 没有 stdout，子进程调 exe 跑 worker 时
    #   输出走 --out 临时文件（见 worker_bridge.run）。
    if len(sys.argv) > 1 and sys.argv[1] == '--worker':
        return wb.worker_cli(sys.argv[2:])
    # 诊断模式：在进程内跑 on_fetch 核心步骤并落盘
    #   （用于排查「exe 点读取库数据不转明」这类只在 frozen 下出现的问题）
    if len(sys.argv) > 1 and sys.argv[1] == '--selftest':
        return selftest()
    App()
    return 0


if __name__ == '__main__':
    sys.exit(main())
