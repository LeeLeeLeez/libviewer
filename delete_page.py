# -*- coding: utf-8 -*-
"""删除页（LibViewer）—— 出栈 / 入栈式删除明文库里的符号与封装。

设计要点
========
1. **两个子 tab**：`原理图封装`（删 SchLib 的符号 = 整个物料记录）
                  `PCB封装`（删 PcbLib 的封装）。
2. **出栈即暂存，点提交才真删**：左「候选」把条目挪到右「待删除」，
   只有按「提交删除」才落盘。
3. **单次数量硬上限 ≤ MAX_BATCH(6)**：超了就拒绝提交（见 lib_delete 说明）。
   这是**正确性**限制 —— OLE 目录红黑树摘除在多删时会产生孤儿节点。
4. 🔴 **所有实际删除都交给外部进程**（用装了 olefile 的 venv 跑 _del_worker.py）。
   本 GUI 进程（系统 Python 312）没有 olefile，import lib_delete 会 `ModuleNotFoundError`；
   而且这样即使 worker 崩了，GUI 也不受影响。
5. 🔴 **每次提交前强制备份**（`<lib>.bak_del_<stamp>`，由 lib_delete 生成），
   不提供「不备份」选项。
6. 🔴 提交后**不自动重建缓存**（避免误触发重型流程）；提示用户手动点「读取库数据」。

数据来源：`main.py` 的 `app.data['categories']`（= lib_cache.json）。
落盘目标：`lib_reader.resolve_sources()` 解析出的**正本明文**路径
          （绝不用 history/backup —— 那是快照，改它没意义）。
"""
import os
import sys
import json
import queue
import threading
import subprocess
import tkinter as tk
from tkinter import ttk
from tkinter import messagebox
tk.messagebox = messagebox          # 便于内部统一用 tk.messagebox.xxx

import gui_style as gs
import worker_bridge as wb

# 🔴 打包后 HERE 必须 = exe 所在目录（不是 sys._MEIPASS），
#   否则 _del_worker.py 会指到临时解包目录、找不到脚本。
if getattr(sys, 'frozen', False):
    HERE = os.path.dirname(os.path.abspath(sys.executable))
else:
    HERE = os.path.dirname(os.path.abspath(__file__))

PAL = gs.PALETTE

# 装了 olefile 的解释器（GUI 自身不需要）
WORK_PY = r'C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
WORKER = os.path.join(HERE, '_del_worker.py')
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

MAX_BATCH = 6          # 🔴 必须与 lib_delete.MAX_BATCH 一致


def _utf8_env():
    """返回一份注入了 UTF-8 环境变量的 env。

    🔴🔴 本机 Windows 默认 locale = cp936(GBK)。用户双击 .cmd 启动时
    环境里没有 PYTHONUTF8/PYTHONIOENCODING，worker 的 print 会按 GBK 编码，
    而本文件用 encoding='utf-8' 解码 → 中文全乱码。
    这里在调子进程时把它钉死，与 _del_worker.py 里的 reconfigure 双保险。
    """
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


# ==================================================================== 通用小件
class StackPanel(ttk.Frame):
    """两栏「候选 / 待删」之间的出入栈面板（单个库）。

    左：可搜的候选列表；右：待删栈。
    双击左侧条目 → 入栈；双击右侧条目 → 出栈。
    """

    def __init__(self, parent, title, on_change=None):
        super().__init__(parent)
        self.title = title
        self.on_change = on_change
        self.all = []            # 全部候选（str 列表）
        self.stack = []          # 待删（有序）
        self._build()

    def _build(self):
        # 标题行
        bar = ttk.Frame(self)
        bar.pack(fill='x', padx=6, pady=(4, 2))
        ttk.Label(bar, text=self.title, style='Card.TLabel').pack(side='left')
        self.cnt = ttk.Label(bar, text='', style='Muted.TLabel')
        self.cnt.pack(side='right')

        pw = gs.paned(self, orient='h', sash=6)
        pw.pack(fill='both', expand=True, padx=4, pady=(0, 4))

        # ── 左：候选
        left = tk.Frame(pw, bg=PAL['surface'])
        pw.add(left, minsize=int(210 * gs.SCALE))
        lf = ttk.Frame(left)
        lf.pack(fill='x', padx=4, pady=(4, 2))
        ttk.Label(lf, text='候选（双击 → 入栈）',
                  style='Muted.TLabel').pack(side='left')
        self.var = tk.StringVar()
        self.var.trace_add('write', lambda *_: self._refill())
        ttk.Entry(lf, textvariable=self.var, width=14, style='Search.TEntry',
                  font=gs.font(9)).pack(side='right')
        lb = tk.Frame(left, bg=PAL['border'])
        lb.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        lbx = tk.Frame(lb, bg=PAL['surface'])
        lbx.pack(fill='both', expand=True, padx=1, pady=1)
        self.lb_src = tk.Listbox(lbx, **gs.listbox_kw())
        vs = ttk.Scrollbar(lbx, orient='vertical', command=self.lb_src.yview)
        self.lb_src.configure(yscrollcommand=vs.set)
        self.lb_src.pack(side='left', fill='both', expand=True)
        vs.pack(side='right', fill='y')
        self.lb_src.bind('<Double-1>', lambda _e: self.push())

        # ── 中：按钮列
        mid = tk.Frame(pw, bg=PAL['bg'])
        pw.add(mid, minsize=int(58 * gs.SCALE))
        ttk.Button(mid, text='入栈 →', style='Tool.TButton',
                   command=self.push).pack(padx=4, pady=(40, 3))
        ttk.Button(mid, text='← 出栈', style='Tool.TButton',
                   command=self.pop).pack(padx=4, pady=3)
        ttk.Button(mid, text='全部入栈', style='Tool.TButton',
                   command=self.push_all).pack(padx=4, pady=3)
        ttk.Button(mid, text='清空栈', style='Tool.TButton',
                   command=self.clear).pack(padx=4, pady=3)

        # ── 右：待删栈
        right = tk.Frame(pw, bg=PAL['surface'])
        pw.add(right, minsize=int(210 * gs.SCALE))
        rf = ttk.Frame(right)
        rf.pack(fill='x', padx=4, pady=(4, 2))
        ttk.Label(rf, text='待删除（双击 → 出栈）',
                  style='Muted.TLabel').pack(side='left')
        rb = tk.Frame(right, bg=PAL['border'])
        rb.pack(fill='both', expand=True, padx=4, pady=(0, 4))
        rbx = tk.Frame(rb, bg=PAL['surface'])
        rbx.pack(fill='both', expand=True, padx=1, pady=1)
        self.lb_dst = tk.Listbox(rbx, **gs.listbox_kw())
        self.lb_dst.configure(
            selectbackground=PAL['accent_soft'],
            selectforeground=PAL['text'],
            foreground=PAL['danger'])
        vs2 = ttk.Scrollbar(rbx, orient='vertical', command=self.lb_dst.yview)
        self.lb_dst.configure(yscrollcommand=vs2.set)
        self.lb_dst.pack(side='left', fill='both', expand=True)
        vs2.pack(side='right', fill='y')
        self.lb_dst.bind('<Double-1>', lambda _e: self.pop())

    # ---------------------------------------------------------- 数据
    def set_items(self, items):
        """items: 候选名列表（会去重并排序）。"""
        self.all = sorted(set(items))
        self.stack = [x for x in self.stack if x in set(self.all)]
        self._refill()

    # ------------------------------------------------- 列表位置保持
    # 🔴 为什么不用 see()：see() 只在条目不可见时才滚动，已可见时是 no-op，
    #    无法还原「某行恰好停在顶部」这种精确位置（_probe_lb2.py 实测）。
    #    唯一能精确对位的是 yview_moveto(行号 / 总数)。
    #
    # 🔴 为什么用「行号」而不是「名字」当锚点：
    #    入栈会把选中那行**移走**。若锚点存名字，而名字正好是被移走的那个，
    #    就找不到参照 → 只能回落列表头 → 跳顶（连点入栈必现）。
    #    改存行号后，「第 N 行被移走 → 新的第 N 行顶上来」，语义正合直觉。
    @staticmethod
    def _top_idx(lb):
        """当前最上可见行的**行号**。"""
        try:
            return 0 if lb.size() == 0 else lb.nearest(0)
        except Exception:
            return 0

    @staticmethod
    def _sel_idx(lb):
        """当前选中行的行号（无选中 → None）。"""
        try:
            c = lb.curselection()
            return c[0] if c else None
        except Exception:
            return None

    @staticmethod
    def _moveto(lb, idx):
        """把第 idx 行滚到顶部（越界自动夹取）。"""
        try:
            n = lb.size()
            if n == 0 or idx is None:
                return
            idx = max(0, min(idx, n - 1))
            lb.yview_moveto(idx / float(n))
        except Exception:
            pass

    @staticmethod
    def _select_idx(lb, idx):
        """按行号选中（越界返回 False）。"""
        try:
            n = lb.size()
            if n == 0 or idx is None:
                return False
            idx = max(0, min(idx, n - 1))
            lb.selection_clear(0, 'end')
            lb.selection_set(idx)
            lb.activate(idx)
            return True
        except Exception:
            return False

    def _refill(self):
        """重建两栏内容。

        🔴 重建（delete+insert）会清空选择并把滚动归零 —— 所以必须先记下
           「顶部行号 / 选中行号 / 选中名字」，重建后再对位还原。
           否则用户点一次「入栈」，光标就跳回列表最上面（用户 2026-09-24 反馈）。
        """
        g = self.var.get().strip().lower()
        low = set(self.stack)

        # ---- 重建前快照 ----
        src_top_i = self._top_idx(self.lb_src)
        src_sel_i = self._sel_idx(self.lb_src)
        src_sel_n = (self.lb_src.get(src_sel_i)
                     if src_sel_i is not None and self.lb_src.size() else None)
        dst_top_i = self._top_idx(self.lb_dst)
        dst_sel_i = self._sel_idx(self.lb_dst)

        # ---- 重建左栏（候选）----
        self.lb_src.delete(0, 'end')
        shown = 0
        for n in self.all:
            if n in low:
                continue
            if g and g not in n.lower():
                continue
            self.lb_src.insert('end', n)
            shown += 1

        # ---- 重建右栏（待删栈）----
        self.lb_dst.delete(0, 'end')
        for k, n in enumerate(self.stack, 1):
            self.lb_dst.insert('end', '%2d. %s' % (k, n))

        # ---- 还原左栏 ----
        n_src = self.lb_src.size()
        if n_src:
            names = self.lb_src.get(0, 'end')
            if src_sel_n is not None and src_sel_n in names:
                # ① 原选中项还在（例如只是打字过滤）→ 名对位，最准
                new_sel = names.index(src_sel_n)
                self._moveto(self.lb_src, src_top_i)
                self._select_idx(self.lb_src, new_sel)
            else:
                # ② 原选中项被移出候选（刚入栈）→ 顶行号不动：
                #    移除点若在锚点之前，锚点整体前移 1；否则原地不动。
                drop = src_sel_i if src_sel_i is not None else src_top_i
                anchor = src_top_i - 1 if drop < src_top_i else src_top_i
                self._moveto(self.lb_src, anchor)
                # 光标停在新锚点那行（= 顶上来占位的下一条），连点可一路往下
                self._select_idx(self.lb_src, anchor)

        # ---- 还原右栏（栈顺序只增删，行号对位足够）----
        self._moveto(self.lb_dst, dst_top_i)
        n_dst = self.lb_dst.size()
        if n_dst:
            if dst_sel_i is not None and not self._select_idx(self.lb_dst,
                                                              dst_sel_i):
                self._select_idx(self.lb_dst, n_dst - 1)

        self.cnt.config(
            text='候选 %d（显示 %d）　待删 %d / %d'
                 % (len(self.all), shown, len(self.stack), MAX_BATCH))
        if self.on_change:
            self.on_change()

    # ---------------------------------------------------------- 操作
    def push(self):
        s = self.lb_src.curselection()
        if not s:
            return
        n = self.lb_src.get(s[0])
        if n not in self.stack:
            self.stack.append(n)
        self._refill()

    def push_all(self):
        """把当前**筛选后可见**的候选全部入栈（仍受 MAX_BATCH 约束，超了会拒绝提交）。"""
        g = self.var.get().strip().lower()
        low = set(self.stack)
        for n in self.all:
            if n in low:
                continue
            if g and g not in n.lower():
                continue
            self.stack.append(n)
            low.add(n)
        self._refill()

    def pop(self):
        s = self.lb_dst.curselection()
        if not s:
            return
        del self.stack[s[0]]
        self._refill()

    def clear(self):
        self.stack = []
        self._refill()

    def names(self):
        return list(self.stack)


class DeletePage(ttk.Frame):
    """删除页 —— 两个子 tab（原理图封装 / PCB封装）。"""

    TITLE = '删除　—　从明文库移除条目（出栈即暂存，点提交才真删）'
    SUBTITLE = (
        '用法：左侧「候选」双击条目 → 入栈到右侧「待删除」；右侧双击可退回。'
        '确认无误后按「提交删除」。\n'
        '护栏：\n'
        '  · 单次最多删 %d 个（OLE 目录红黑树摘除在多删时会留下孤儿节点，属已知缺陷）；\n'
        '  · 每次提交**强制备份** <库文件>.bak_del_<时间戳>，出问题可直接回滚；\n'
        '  · 只接受**正本明文**库（密文库请先转明文）；\n'
        '  · 提交期间请**不要**在 AD 里打开这些库（AD 缓冲会在下次保存时整库覆盖）；\n'
        '  · 本页**绝不**调用 app_save_all。' % MAX_BATCH)

    def __init__(self, parent, app):
        super().__init__(parent)
        self.app = app
        self._q = queue.Queue()
        self._busy = False
        self._libs = {}          # 'cat|sch' -> dict(path,ok,name,count,items,why)
        self.panels = {}         # (cat, kind) -> StackPanel
        self.trees = {}          # kind -> Treeview
        self.holders = {}        # kind -> card frame
        self._cur = None
        self._keep_stack = True   # reload() 是否保留已入栈条目
        self._build()
        self.reload(keep_stack=False)   # 首次建页无栈可保

    # ---------------------------------------------------------- 布局
    def _build(self):
        head = tk.Frame(self, bg=PAL['bg'])
        head.pack(fill='x', padx=14, pady=(12, 2))
        ttk.Label(head, text=self.TITLE, style='Title.TLabel').pack(side='left')
        ttk.Button(head, text='刷新库状态', style='Tool.TButton',
                   command=self.reload).pack(side='right')

        tk.Label(self, bg=PAL['bg'], fg=PAL['muted'], justify='left',
                 font=gs.font(9), text=self.SUBTITLE).pack(
                     anchor='w', padx=14, pady=(2, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill='both', expand=True, padx=10, pady=(0, 8))
        self.nb = nb

        for key, label in (('sch', '原理图封装'), ('pcb', 'PCB封装')):
            page = ttk.Frame(nb)
            nb.add(page, text='   %s   ' % label)
            self._build_tab(page, key)

        # 底部操作条
        foot = tk.Frame(self, bg=PAL['bg'])
        foot.pack(fill='x', padx=14, pady=(0, 10))

        self.lab_status = ttk.Label(foot, text='', style='Muted.TLabel')
        self.lab_status.pack(side='left')

        self.btn_dry = ttk.Button(foot, text='预演（不落盘）', style='Tool.TButton',
                                  command=lambda: self.submit(dry=True))
        self.btn_dry.pack(side='right', padx=(8, 0))
        self.btn_go = ttk.Button(foot, text='提交删除', style='Accent.TButton',
                                 command=lambda: self.submit(dry=False))
        self.btn_go.pack(side='right')

        # 日志
        box = gs.card(self, '执行日志')
        box.pack(fill='both', expand=True, padx=10, pady=(0, 10))
        self.log = tk.Text(box, height=7, **gs.text_kw())
        vs = ttk.Scrollbar(box, orient='vertical', command=self.log.yview)
        self.log.configure(yscrollcommand=vs.set)
        self.log.pack(side='left', fill='both', expand=True, padx=(6, 0), pady=6)
        vs.pack(side='right', fill='y', pady=6, padx=(0, 6))
        self.log.configure(state='disabled')

    def _build_tab(self, parent, kind):
        """一个子 tab = 左「类别列表」+ 右「该类别出栈入栈面板」。"""
        pw = gs.paned(parent, orient='h', sash=8)
        pw.pack(fill='both', expand=True, padx=8, pady=8)

        # 左：类别
        left = gs.card(pw, '类别')
        pw.add(left, minsize=int(200 * gs.SCALE))
        cols = ('lib',)
        tv = ttk.Treeview(left, columns=cols, show='tree headings',
                          style='Lib.Treeview', selectmode='browse')
        tv.heading('#0', text='')
        tv.heading('lib', text='库')
        tv.column('#0', width=int(20 * gs.SCALE), stretch=False)
        tv.column('lib', width=int(150 * gs.SCALE), anchor='w', stretch=True)
        vs = ttk.Scrollbar(left, orient='vertical', command=tv.yview)
        tv.configure(yscrollcommand=vs.set)
        tv.pack(side='left', fill='both', expand=True, padx=(6, 0), pady=6)
        vs.pack(side='right', fill='y', pady=6, padx=(0, 6))
        tv.bind('<<TreeviewSelect>>', lambda _e, k=kind: self._pick(k))
        self.trees[kind] = tv

        # 右：面板容器（切换类别时重建）
        right = gs.card(pw, '出栈 / 入栈')
        pw.add(right, minsize=int(520 * gs.SCALE))
        self.holders[kind] = right

    # ---------------------------------------------------------- 数据
    def reload(self, keep_stack=True):
        """从 lib_reader 解析各库正本路径 + 读名单（走外部进程，因 GUI 无 olefile）。

        keep_stack=True 时，读取完成后**保留**用户已入栈的待删条目
        （只保留在新名单里仍存在的），避免 Home 页「读取库数据」触发
        全页刷新时把用户挑好的清单静默清空。
        """
        if self._busy:
            return
        self._keep_stack = bool(keep_stack)
        self._set_busy(True, '正在读取库状态…')
        threading.Thread(target=self._reload_worker, daemon=True).start()
        self.after(120, self._pump)

    def _reload_worker(self):
        """在装了 olefile 的子进程里跑：解析路径 + 读名单 → JSON。"""
        try:
            _rc, _out, _err = wb.run('list', timeout=300)
            out = type('X', (), {'returncode': _rc, 'stdout': _out,
                                 'stderr': _err})()
            if _rc != 0:
                self._q.put(('log', '读取失败：%s' % (_err or _out)[:400]))
                self._q.put(('busy', False, '读取失败'))
                return
            data = json.loads(out.stdout)
            self._q.put(('libs', data))
            self._q.put(('busy', False, '就绪'))
        except Exception as ex:
            self._q.put(('log', '读取异常：%r' % (ex,)))
            self._q.put(('busy', False, '读取异常'))

    def _pump(self):
        try:
            while True:
                msg = self._q.get_nowait()
                tag = msg[0]
                if tag == 'log':
                    self._log(msg[1])
                elif tag == 'busy':
                    self._set_busy(msg[1], msg[2])
                elif tag == 'libs':
                    self._apply_libs(msg[1])
                elif tag == 'result':
                    self._apply_result(*msg[1:])
        except queue.Empty:
            pass
        if self._busy:
            self.after(120, self._pump)

    def _apply_libs(self, data):
        # 刷新前先记下各面板已入栈的条目（键 -> [名字…]），
        # 新名单读回后凡是仍存在的就重新入栈。
        keep = {}
        if self._keep_stack:
            for key, p in list(self.panels.items()):
                try:
                    nm = p.names()
                except Exception:
                    nm = []
                if nm:
                    keep[key] = nm

        self._libs = data
        order = self.app_order()
        n_cache = len(getattr(self.app, 'data', {}).get('categories', []) or [])
        miss_all = []
        self._log('库根目录：%s' % self._base())
        self._log('本进程 cache 类别 %d 个 → 并集后 %d 个 / 外部进程回报 %d 项'
                  % (n_cache, len(order), len(data)))
        if n_cache != len(order):
            self._log('⚠ cache 类别数与并集不一致（cache %d / 实际 %d）—— '
                      'cache 可能过期或被截断，已按外部进程现读结果补齐。'
                      % (n_cache, len(order)))
        # 填充两个类别的树
        for kind, tv in self.trees.items():
            tv.delete(*tv.get_children())
            n_ok = n_bad = n_miss = 0
            for cat in order:
                info = data.get('%s|%s' % (cat, kind))
                if not info:
                    # 🔴 键对不上：唯一可能是 worker 用的 base/类别表与本进程不同
                    n_miss += 1
                    miss_all.append('%s|%s' % (cat, kind))
                    continue
                if info['ok']:
                    n_ok += 1
                    tv.insert('', 'end', iid=cat,
                              text='●', values=('%s（%d）'
                                                % (info['name'], info['count']),))
                else:
                    n_bad += 1
                    tv.insert('', 'end', iid=cat + '#bad',
                              text='○', values=('%s ✘ %s'
                                                % (info['name'], info['why']),))
            self._log('%s：%d 个可删库，%d 个不可用%s'
                      % ({'sch': '原理图封装', 'pcb': 'PCB封装'}[kind],
                         n_ok, n_bad,
                         ('，%d 个键对不上' % n_miss) if n_miss else ''))
        if miss_all:
            self._log('⚠ 键对不上明细：%s' % ', '.join(miss_all[:8]))
            self._log('⚠ 说明子进程用的库根目录/类别表与本进程不一致 —— '
                      '检查环境变量 LIBVIEWER_BASE 与 libviewer_cfg.json')
        # 默认选中：**优先「原理图封装」(sch)，没有可用库才退到 pcb**
        # （与第一个子 tab 一致，符合「从左到右」的阅读习惯）。
        #
        # 🔴 必须显式调 _pick()：`tv.selection_set()` **不会**触发
        #    `<<TreeviewSelect>>`（那是用户交互事件），所以只设选中项的话
        #    self._cur 会一直是 None → 面板不建 + 「提交删除」永远 disabled。
        #    ← 这就是用户「提交删除按钮始终点不了」的根因。
        #
        # 🔴 不能写 `for kind in ('sch','pcb'): ... _pick(kind)`：
        #    两次都调，后一次 (pcb) 会覆盖前一次，默认就落到 pcb 了。
        #    要么只 pick 第一个有货的，要么反向遍历。这里取前者，语义最清楚。
        for kind in ('sch', 'pcb'):
            tv = self.trees.get(kind)
            if tv is None:
                continue
            kids = tv.get_children()
            if not kids:
                continue
            tv.selection_set(kids[0])
            tv.focus(kids[0])
            try:
                self._pick(kind)
            except Exception as _e:
                self._log('   默认选中失败：%r' % _e)
            break        # 🔴 只认第一个「有可用库」的 tab，别被后一个覆盖

        # 恢复刷新前的入栈条目（只保留新名单里仍有的）
        if keep:
            n_back = 0
            for key, nm in keep.items():
                cat, kind = key
                info = data.get('%s|%s' % (cat, kind))
                if not info or not info.get('ok'):
                    continue
                valid = set(info.get('items') or [])
                still = [x for x in nm if x in valid]
                gone = [x for x in nm if x not in valid]
                if gone:
                    self._log('   刷新：%s/%s 待删清单里有 %d 项已不存在，'
                              '已移除：%s'
                              % (cat,
                                 {'sch': '原理图封装',
                                  'pcb': 'PCB封装'}[kind],
                                 len(gone), '、'.join(gone[:6])))
                if still:
                    if key not in self.panels:
                        continue
                    self.panels[key].stack = list(still)
                    try:
                        self.panels[key]._refill()
                    except Exception:
                        pass
                    n_back += len(still)
            if n_back:
                self._log('   刷新：已保留待删清单 %d 项' % n_back)
            self._status()

    def _base(self):
        """本进程认定的库根目录（与 lib_reader.get_base() 同源）。"""
        try:
            b = getattr(self.app, 'data', {}).get('lib_base')
            if b:
                return b
        except Exception:
            pass
        return os.environ.get('LIBVIEWER_BASE') or '(未定)'

    def app_order(self):
        """类别顺序。

        🔴 双源：① 本进程 lib_cache.json 的 categories（与左侧导航一致）；
                  ② 外部进程回报的 `cat|kind` 键（= lib_reader.CATEGORIES）。
        两者取**并集** —— 因为曾遇到「本进程 cache 只有 1 个类别，
        但库里明明有 8 个」的情况（GUI 持有的 cache 是旧的/被截断的，
        而 worker 每次都现读 lib_reader）。取并集后至少不会「只剩 1 个可删库」。
        """
        seen = []
        try:
            for c in self.app.data.get('categories', []):
                cat = c.get('cat')
                if cat and cat not in seen:
                    seen.append(cat)
        except Exception:
            pass
        # ② 从 worker 回报的键里补
        for k in self._libs:
            cat = k.split('|', 1)[0]
            if cat and cat not in seen:
                seen.append(cat)
        try:
            from main import ORDER
        except Exception:
            ORDER = []
        return [c for c in ORDER if c in seen] + \
               [c for c in seen if c not in ORDER]

    # ---------------------------------------------------------- 交互
    def _pick(self, kind):
        tv = self.trees[kind]
        s = tv.selection()
        if not s:
            return
        cat = s[0]
        if cat.endswith('#bad'):
            return
        info = self._libs.get('%s|%s' % (cat, kind))
        if not info or not info['ok']:
            return
        holder = self.holders[kind]
        key = (cat, kind)

        # 🔴 切换类别时**释放**旧面板：只隐藏不够 —— 一旦销毁其子树，
        #    self.panels 里缓存的 StackPanel 就成了「widget 已死」的空壳，
        #    下次再 set_items 会 TclError: invalid command name ...listbox。
        #    所以这里对**每个类别各留一个面板**，切走时销毁并移出缓存。
        for k in [k for k in self.panels if k[1] == kind and k != key]:
            try:
                self.panels[k].destroy()
            except Exception:
                pass
            del self.panels[k]

        if key not in self.panels:
            p = StackPanel(holder,
                           '%s　%s　(%d 项)' % (cat, info['name'], info['count']),
                           on_change=self._status)
            self.panels[key] = p
        p = self.panels[key]
        p.set_items(info['items'])
        p.pack(fill='both', expand=True)
        self._cur = key
        self._status()

    def _all_stacks(self):
        """所有面板的待删清单：{(cat, kind): [names]}（只含非空的）。"""
        out = {}
        for key, p in self.panels.items():
            try:
                nm = p.names()
            except Exception:
                nm = []
            if nm:
                out[key] = nm
        return out

    def _total_stack(self):
        """全部类别加起来的待删总数。"""
        return sum(len(v) for v in self._all_stacks().values())

    def _status(self):
        """刷新状态文字 + 按钮可用性。

        🔴 不要 `if not self._cur: return` —— 那样 _cur 为空时按钮会**永远**
           disabled（用户报「提交删除按钮始终点不了」的直接原因）。
           改为：_cur 为空时用**全局待删总数**判定，只要 1..MAX_BATCH 就允许提交。
        """
        p = self.panels.get(self._cur) if self._cur else None
        n = len(p.names()) if p else 0
        total = self._total_stack()

        # 可否提交：本轮以「当前面板」为主口径（提交只作用于当前库），
        # 但只要总数在 1..MAX_BATCH 内且当前面板有选中，就允许。
        ok = 0 < n <= MAX_BATCH
        warn = ''
        if n and n > MAX_BATCH:
            warn = '　⚠ 超限，无法提交'
        elif not n and total:
            warn = '　⚠ 当前类别没选，请先选一个类别'

        cur_txt = ('当前：%s / %s' % (
            self._cur[0],
            {'sch': '原理图封装', 'pcb': 'PCB封装'}[self._cur[1]]) if self._cur
            else '当前：未选类别')
        self.lab_status.config(
            text='%s　待删 %d 个%s%s'
                 % (cur_txt, n, warn,
                    ('　（全部类别共 %d 个）' % total) if total != n else ''),
            style='Muted.TLabel')
        self.btn_go.state(['!disabled'] if ok else ['disabled'])
        self.btn_dry.state(['!disabled'] if n else ['disabled'])

    def _set_busy(self, busy, text):
        self._busy = busy
        if busy:
            self.btn_go.state(['disabled'])
            self.btn_dry.state(['disabled'])
            self.lab_status.config(text=text)
        else:
            # 🔴 先设文字（用户看到「就绪」），再 _status() 覆盖为带计数的状态；
            #    顺序反了会把 _status 写好的计数文字又盖回「就绪」。
            self.lab_status.config(text=text)
            self._status()

    def _log(self, s):
        self.log.configure(state='normal')
        self.log.insert('end', s.rstrip() + '\n')
        self.log.see('end')
        self.log.configure(state='disabled')

    # ---------------------------------------------------------- 提交
    def submit(self, dry=False):
        if self._busy or not self._cur:
            return
        cat, kind = self._cur
        p = self.panels.get(self._cur)
        names = p.names() if p else []
        info = self._libs.get('%s|%s' % (cat, kind))
        if not names or not info or not info['ok']:
            return
        if len(names) > MAX_BATCH:
            self._log('✘ 超限：本次 %d 个 > %d。请分批。' % (len(names), MAX_BATCH))
            return

        verb = '预演' if dry else '提交'
        if not dry:
            ok = tk.messagebox.askyesno(
                '确认删除',
                '即将从以下库删除 %d 个条目：\n\n%s\n%s\n\n'
                '（1）操作前会自动备份为\n    %s.bak_del_<时间戳>\n'
                '（2）请确认 AD 未打开该库\n'
                '（3）删除后需要手动点「读取库数据」刷新界面\n\n确定继续？'
                % (len(names), info['name'],
                   '\n'.join('  · ' + n for n in names),
                   info['name']),
                parent=self.winfo_toplevel())
            if not ok:
                self._log('已取消。')
                return

        self._set_busy(True, '%s中…' % verb)
        threading.Thread(target=self._submit_worker,
                         args=(info['path'], kind, names, dry),
                         daemon=True).start()
        self.after(120, self._pump)

    def _submit_worker(self, path, kind, names, dry):
        sub = 'sch' if kind == 'sch' else 'pcb'
        args = ['--lib', path, '--names', ','.join(names)]
        if dry:
            args.append('--dry')
        try:
            _rc, _out, _err = wb.run(sub, args, timeout=300)
            self._q.put(('log', (_out or '').rstrip()))
            if _rc != 0:
                self._q.put(('log', '✘ 失败：%s' % (_err or '')[:400]))
            else:
                for ln in (_out or '').splitlines():
                    if ln.strip().startswith('@{'):
                        try:
                            r = json.loads(ln.strip()[1:])
                            self._q.put(('result', r, path, kind, names, dry))
                        except Exception:
                            pass
        except Exception as ex:
            self._q.put(('log', '✘ 异常：%r' % (ex,)))
        self._q.put(('busy', False, '完成' if not dry else '预演完成'))

    def _apply_result(self, r, path, kind, names, dry):
        if dry:
            self._log('预演结果：%s' % r.get('msg', ''))
            return
        if not r.get('ok'):
            self._log('✘ 未删除：%s' % r.get('msg', ''))
            return
        self._log('✅ 已删除 %d 个；备份 → %s'
                  % (r.get('deleted', 0), r.get('backup', '-')))
        self._log('   ⚠ 请确认 AD 未打开该库；然后点 Home 页「读取库数据」刷新。')

        # 定位是哪个 (cat, kind)，更新内存中的候选，让界面立刻反映
        key = None
        for k, v in self._libs.items():
            if v.get('path') == path:
                key = k
                break
        if key:
            cat = key.split('|')[0]
            info = self._libs[key]
            info['items'] = [x for x in info['items'] if x not in names]
            info['count'] = len(info['items'])
            p = self.panels.get((cat, kind))
            if p:
                p.stack = [x for x in p.stack if x not in names]
                p.set_items(info['items'])
            tv = self.trees.get(kind)
            if tv and tv.exists(cat):
                tv.item(cat, values=('%s（%d）'
                                     % (info['name'], info['count']),))
        self._status()
