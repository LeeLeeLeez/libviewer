"""自检：「提交删除」按钮的可用性全生命周期。

用户报障（2026-09-24）：「提交删除按钮始终点不了」
根因：`_apply_libs` 用 `tv.selection_set()` 设默认选中，但该 API **不触发**
      `<<TreeviewSelect>>` → `_cur` 恒为 None → `_status()` 早退 → 按钮永远 disabled。

跑法（系统 312）：
    C:\\Users\\admin\\AppData\\Local\\Programs\\Python\\Python312\\python.exe _btnstate_check.py

不连 AD、不落盘。
"""
import json
import os
import subprocess
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


def enabled(btn):
    return 'disabled' not in btn.state()


import tkinter as tk
import gui_style as gs

app = gs.init('btnstate-check')
app.geometry('1000x640')

import delete_page as DPmod

DATA = json.load(open(os.path.join(HERE, 'lib_cache.json'), encoding='utf-8'))


class StubApp:
    def __init__(self, root):
        self.root = root
        self.data = DATA


def real_list():
    """真跑一次外部 worker（venv），拿真实库状态。"""
    out = subprocess.run([DPmod.WORK_PY, DPmod.WORKER, '--mode', 'list'],
                         cwd=HERE, capture_output=True, text=True,
                         encoding='utf-8', errors='replace', timeout=180,
                         creationflags=DPmod._NO_WINDOW)
    assert out.returncode == 0, out.stderr[:300]
    return json.loads(out.stdout)


print('==== A. 建页初期（worker 还没回来）====')
dp = DPmod.DeletePage(app, StubApp(app))
app.update_idletasks()
print('   _cur =', dp._cur, ' btn_go =', dp.btn_go.state(),
      ' btn_dry =', dp.btn_dry.state())
check('A1 _cur 初始为 None', dp._cur is None)
check('A2 提交按钮初始 disabled（无数据可删，合理）', not enabled(dp.btn_go))
check('A3 预演按钮初始 disabled', not enabled(dp.btn_dry))
check('A4 状态文字不崩', isinstance(dp.lab_status.cget('text'), str))

print('==== B. 喂真实库数据（模拟 worker 回报）====')
data = real_list()
dp._libs = {}
dp._apply_libs(data)
app.update_idletasks()

print('   _cur =', dp._cur)
check('B1 🔴 _cur 已就位（本次 bug 的核心）', dp._cur is not None, str(dp._cur))
check('B2 默认落在「原理图封装」sch', dp._cur and dp._cur[1] == 'sch',
      str(dp._cur))
check('B3 默认类别 = 第一个（01电阻）',
      dp._cur and dp._cur[0] == '01电阻', str(dp._cur))
check('B4 对应面板已建', (dp._cur in dp.panels), str(list(dp.panels)))
p = dp.panels.get(dp._cur)
check('B5 面板有候选', p is not None and len(p.all) > 0,
      'n=%s' % (len(p.all) if p else None))
check('B6 未挑条目 → 按钮仍 disabled（合理）', not enabled(dp.btn_go))
check('B7 预演也 disabled', not enabled(dp.btn_dry))
check('B8 状态文字含类别名',
      '01电阻' in dp.lab_status.cget('text'), dp.lab_status.cget('text'))

print('==== C. 入栈 → 按钮应可点（用户核心诉求）====')
first = p.all[0]
p.stack = [first]
p._refill()
dp._status()
app.update_idletasks()
print('   入栈 1 个：btn_go =', dp.btn_go.state(), ' btn_dry =', dp.btn_dry.state())
check('C1 🔴 入栈 1 个 → 提交按钮可点', enabled(dp.btn_go),
      str(dp.btn_go.state()))
check('C2 预演按钮也可点', enabled(dp.btn_dry), str(dp.btn_dry.state()))
check('C3 状态文字显示待删 1 个',
      '待删 1 个' in dp.lab_status.cget('text'), dp.lab_status.cget('text'))

print('==== D. 超过 MAX_BATCH 应禁用 ====')
n_max = DPmod.MAX_BATCH
p.stack = p.all[:n_max]
p._refill(); dp._status()
app.update_idletasks()
check('D1 正好 %d 个 → 可点' % n_max, enabled(dp.btn_go), str(dp.btn_go.state()))
p.stack = p.all[:n_max + 1]
p._refill(); dp._status()
app.update_idletasks()
print('   %d 个：btn_go = %s  status = %s'
      % (n_max + 1, dp.btn_go.state(), dp.lab_status.cget('text')))
check('D2 超限 %d 个 → 禁用' % (n_max + 1), not enabled(dp.btn_go),
      str(dp.btn_go.state()))
check('D3 超限有明确提示', '超限' in dp.lab_status.cget('text'),
      dp.lab_status.cget('text'))

print('==== E. 降回合法数量 → 恢复可点 ====')
p.stack = p.all[:2]
p._refill(); dp._status()
app.update_idletasks()
check('E1 降到 2 个 → 恢复可点', enabled(dp.btn_go), str(dp.btn_go.state()))

print('==== F. _set_busy 生命周期不吞按钮状态 ====')
dp._set_busy(True, '正在读取库状态…')
app.update_idletasks()
check('F1 busy=True → 按钮临时禁用', not enabled(dp.btn_go))
check('F2 busy 时状态文字 = 传入文字',
      dp.lab_status.cget('text') == '正在读取库状态…',
      dp.lab_status.cget('text'))
dp._set_busy(False, '就绪')
app.update_idletasks()
print('   busy=False 后：btn_go =', dp.btn_go.state(),
      ' text =', dp.lab_status.cget('text'))
check('F3 🔴 busy=False 后按钮恢复可点（不被吞）', enabled(dp.btn_go),
      str(dp.btn_go.state()))
check('F4 busy=False 后状态文字回到计数（不是「就绪」）',
      '待删' in dp.lab_status.cget('text'), dp.lab_status.cget('text'))

print('==== G. 跨类别：栈在别的类别时按钮仍可点 ====')
# 把当前面板清空，改在另一个类别入栈
cur_cat = dp._cur[0]
p.stack = []
p._refill(); dp._status()
app.update_idletasks()
check('G1 清空后 disabled', not enabled(dp.btn_go))

# 找一个不同的类别
other = None
for cat in ('02电容', '05二极管', '06晶体管'):
    if cat != cur_cat:
        other = cat
        break
dp.trees['sch'].selection_set(other)
dp._pick('sch')
app.update_idletasks()
p2 = dp.panels.get((other, 'sch'))
print('   切到 %s/%s，候选 %d 个' % (other, 'sch', len(p2.all) if p2 else -1))
check('G2 切换类别后面板就位', p2 is not None and len(p2.all) > 0,
      str((other, 'sch')))
p2.stack = p2.all[:1]
p2._refill(); dp._status()
app.update_idletasks()
check('G3 新类别入栈后按钮可点', enabled(dp.btn_go), str(dp.btn_go.state()))

print('==== H. _total_stack / _all_stacks 口径 ====')
st = dp._all_stacks()
tot = dp._total_stack()
expect = sum(len(v) for v in st.values())
check('H1 _total_stack = 各面板之和', tot == expect, '%d vs %d' % (tot, expect))
check('H2 只含非空面板', all(len(v) > 0 for v in st.values()), str(st))
print('   _all_stacks =', {('%s|%s' % k): len(v) for k, v in st.items()})
check('H3 当前栈总数 = 1（只在新类别放了 1 个）', tot == 1, 'tot=%d' % tot)

print('==== I. 只建页不喂数据时，_status 不崩 ====')
dp3 = DPmod.DeletePage(app, StubApp(app))
try:
    dp3._status()
    ok = True
except Exception as ex:
    ok = False
    print('   ex=%r' % ex)
check('I1 _cur=None 时 _status 不崩', ok)
check('I2 _cur=None 时按钮 disabled', not enabled(dp3.btn_go))
check('I3 _cur=None 时文字含「未选类别」',
      '未选类别' in dp3.lab_status.cget('text'), dp3.lab_status.cget('text'))
check('I4 _total_stack 空时为 0', dp3._total_stack() == 0)

app.destroy()
print('')
print('共 %d 项，FAIL %d 项' % (N[0], len(FAIL)))
print('RESULT: %s' % ('PASS' if not FAIL else 'FAIL -> %s' % FAIL))
sys.exit(0 if not FAIL else 1)
