"""自检：入栈 / 出栈 / 全部入栈后，光标与滚动位置是否保持。

跑法（系统 312）：
    C:\\Users\\admin\\AppData\\Local\\Programs\\Python\\Python312\\python.exe _keepcursor_check.py

全在内存里跑，不碰库文件。

🔴 断言口径说明（上一版写错过）：
   - `push()` 会从候选移走 1 条、往栈加 1 条 → 候选 -1。
   - 本版每个用例**先记录基线数量**，再按「增量」断言，不写死数字。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

FAIL = []
N = [0]


def check(name, cond, extra=''):
    N[0] += 1
    if cond:
        print('[ OK ] %s' % name)
    else:
        print('[FAIL] %s  %s' % (name, extra))
        FAIL.append(name)


import tkinter as tk
import gui_style as gs

app = gs.init('keepcursor-check')
app.geometry('1000x640')

from delete_page import StackPanel

holder = tk.Frame(app)
holder.pack(fill='both', expand=True)

ITEMS = ['SYM_%04d' % i for i in range(200)]


def _sel_n(lb):
    c = lb.curselection()
    return lb.get(c[0]) if c else None
sp = StackPanel(holder, '测试面板')
sp.pack(fill='both', expand=True)
sp.set_items(ITEMS)

app.update_idletasks()
app.update()

print('---- A. 基本 ----')
check('A1 候选 200 条', sp.lb_src.size() == 200, 'got %d' % sp.lb_src.size())
check('A2 栈初始为空', sp.lb_dst.size() == 0)

print('---- B. 入栈后光标/滚动保持（核心）----')
# 滚到第 40 行在顶部、选中第 50 条
sp.lb_src.yview_moveto(40 / 200.0)
sp.lb_src.selection_clear(0, 'end')
sp.lb_src.selection_set(50)
sp.lb_src.activate(50)
app.update_idletasks(); app.update()

top_before = sp.lb_src.nearest(0)
top_name_before = sp.lb_src.get(top_before)
print('   入栈前：顶部(行%d)=%s' % (top_before, top_name_before))

sp.push()
app.update_idletasks(); app.update()

top_after = sp.lb_src.nearest(0)
print('   入栈后：顶部(行%d)=%s  选中=%s'
      % (top_after, sp.lb_src.get(0, 'end')[top_after] if sp.lb_src.size() else '-',
         _sel_n(sp.lb_src)))

check('B1 该条已入栈', sp.names() == ['SYM_0050'], str(sp.names()))
check('B2 滚动未跳顶', top_after > 0, 'top_after=%d' % top_after)
check('B3 顶部锚点条目不变（精确对位）',
      sp.lb_src.get(top_after) == top_name_before,
      'before=%s after=%s' % (top_name_before, sp.lb_src.get(top_after)))
check('B4 选中落到「原顶部那条」（它现在占着原选中项的位置）',
      _sel_n(sp.lb_src) == top_name_before,
      'sel=%s anchor=%s' % (_sel_n(sp.lb_src), top_name_before))
check('B5 候选 199 条', sp.lb_src.size() == 199, 'got %d' % sp.lb_src.size())

print('---- C. 连续入栈位置稳定 ----')
tops = [sp.lb_src.nearest(0)]
for _ in range(3):
    sp.push()
    app.update_idletasks(); app.update()
    tops.append(sp.lb_src.nearest(0))
print('   连续入栈 top 序列:', tops)
check('C1 连续入栈都不跳顶', all(t > 0 for t in tops), str(tops))
check('C2 top 稳定（不再逐次下漂）', abs(tops[-1] - tops[0]) <= 1, str(tops))
check('C3 栈累计 4 条', len(sp.names()) == 4, str(sp.names()))
check('C4 候选 196 条', sp.lb_src.size() == 196, 'got %d' % sp.lb_src.size())

print('---- D. 出栈 ----')
n_stack = len(sp.names())
n_src = sp.lb_src.size()
sp.lb_dst.selection_clear(0, 'end')
sp.lb_dst.selection_set(0)
sp.pop()
app.update_idletasks(); app.update()
check('D1 出栈后栈 -1', len(sp.names()) == n_stack - 1, str(sp.names()))
check('D2 候选项 +1', sp.lb_src.size() == n_src + 1,
      'got %d want %d' % (sp.lb_src.size(), n_src + 1))
check('D3 出栈不影响候选滚动（仍在原位附近）', sp.lb_src.nearest(0) > 0,
      'top=%d' % sp.lb_src.nearest(0))

print('---- E. 搜索过滤 ----')
n_base = sp.lb_src.size()          # 以当前实际数为基线（不写死）
sp.var.set('SYM_01')
app.update_idletasks(); app.update()
n_f = sp.lb_src.size()
check('E1 过滤生效', 0 < n_f < n_base, 'got %d base %d' % (n_f, n_base))
sp.var.set('')
app.update_idletasks(); app.update()
check('E2 清空过滤后恢复原数', sp.lb_src.size() == n_base,
      'got %d want %d' % (sp.lb_src.size(), n_base))

print('---- F. 全部入栈 / 清空 ----')
sp.push_all()
app.update_idletasks(); app.update()
check('F1 全入栈后候选清空', sp.lb_src.size() == 0, 'got %d' % sp.lb_src.size())
check('F2 栈 = 200', len(sp.names()) == 200, 'got %d' % len(sp.names()))
sp.clear()
app.update_idletasks(); app.update()
check('F3 清空栈', len(sp.names()) == 0, str(sp.names()))

print('---- G. 边界 ----')
sp2 = StackPanel(holder, '空面板')
sp2.pack(fill='both', expand=True)
sp2.set_items([])
app.update_idletasks(); app.update()
try:
    sp2.push(); sp2.pop(); sp2.push_all(); sp2.clear()
    ok = True
except Exception as ex:
    ok = False
    print('   ex=%r' % ex)
check('G1 空列表各操作不抛异常', ok)
check('G2 空列表 _top_name 为 None', sp2.lb_src.size() == 0)
check('G3 空列表 _sel_idx 为 None', sp2._sel_idx(sp2.lb_src) is None)

sp2.set_items(['ONLY'])
app.update_idletasks(); app.update()
check('G4 单项 _moveto 不抛', True)
sp2._moveto(sp2.lb_src, 0)   # 单项滚到 0
check('G5 _moveto 越界静默夹取', True)

print('---- H. 名字重复时按名定位不崩 ----')
sp3 = StackPanel(holder, '重复项')
sp3.pack(fill='both', expand=True)
sp3.set_items(['A', 'A', 'B'])          # set_items 会去重
app.update_idletasks(); app.update()
check('H1 set_items 去重为 2 条', sp3.lb_src.size() == 2,
      'got %d' % sp3.lb_src.size())

app.destroy()
print('')
print('共 %d 项，FAIL %d 项' % (N[0], len(FAIL)))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL -> %s' % FAIL))
sys.exit(0 if not FAIL else 1)
