# -*- coding: utf-8 -*-
"""delete_page 无头自检（用系统 Python 312 跑，需 tkinter）。

不进入 mainloop：手工建 Tk + 一个假 App，构造 DeletePage，跑 reload，
等 worker 回数据，检查两个 tab 的树都填上了。
"""
import os
import sys
import time
import tkinter as tk

HERE = r'E:\AI_model\800_6_VPDB\LibViewer'
sys.path.insert(0, HERE)
os.chdir(HERE)

import gui_style as gs            # noqa: E402
import delete_page as DP          # noqa: E402

FAILS = []


def check(name, cond, extra=''):
    print('  [%s] %-46s %s' % ('OK' if cond else '!!', name, extra))
    if not cond:
        FAILS.append(name)


class FakeApp(object):
    """只有 DeletePage 需要的两样东西：root + data['categories']。"""
    def __init__(self, root):
        self.root = root
        import json
        p = os.path.join(HERE, 'lib_cache.json')
        with open(p, encoding='utf-8') as f:
            self.data = json.load(f)


def main():
    print('=== delete_page 无头自检 ===')
    check('WORKER 存在', os.path.exists(DP.WORKER), DP.WORKER)
    check('WORK_PY 存在', os.path.exists(DP.WORK_PY))
    check('MAX_BATCH == lib_delete.MAX_BATCH', _same_batch())

    root = gs.init('自检', size=(1200, 800))
    root.withdraw()
    app = FakeApp(root)

    page = DP.DeletePage(root, app)
    page.pack(fill='both', expand=True)
    root.update()

    check('两个子 tab 建成', len(page.trees) == 2, str(sorted(page.trees)))
    check('Notebook 有 2 页', len(page.nb.tabs()) == 2)

    # 等 worker 回数据
    t0 = time.time()
    while page._busy and time.time() - t0 < 120:
        root.update()
        time.sleep(0.1)
    root.update()

    check('reload 结束（不再 busy）', not page._busy)
    check('libs 已加载', len(page._libs) == 16, '%d 项' % len(page._libs))

    for kind, label in (('sch', '原理图封装'), ('pcb', 'PCB封装')):
        tv = page.trees[kind]
        kids = tv.get_children()
        check('%s 树有 %d 行' % (label, len(kids)), len(kids) >= 8)

    # 选第一个类别 → 面板应出现
    for kind in ('sch', 'pcb'):
        tv = page.trees[kind]
        kids = tv.get_children()
        if kids:
            tv.selection_set(kids[0])
            root.update()
    root.update()
    n_panel = sum(1 for k in page.panels)
    check('至少 2 个 StackPanel 已建', n_panel >= 2, '%d 个' % n_panel)

    # 出栈入栈 + 超限守卫（在面板上模拟）
    pk = None
    for k, p in page.panels.items():
        if len(p.all) >= 8:
            pk = k
            break
    if pk:
        p = page.panels[pk]
        p.push_all()
        root.update()
        n_all = len(p.names())
        check('全入栈数量正确', n_all == len(p.all), '%d' % n_all)
        page._cur = pk
        page._status()
        root.update()
        lim = int(page.btn_go.state()[0] not in ('disabled',)) if page.btn_go.state() else 0
        check('超限时提交按钮被禁用', 'disabled' in page.btn_go.state(),
              'state=%s' % (page.btn_go.state(),))
        # 出栈到限额内
        while len(p.names()) > DP.MAX_BATCH:
            p.stack.pop()
        p._refill()
        page._status()
        root.update()
        check('降到 %d 个后按钮恢复' % DP.MAX_BATCH,
              'disabled' not in page.btn_go.state(),
              'state=%s' % (page.btn_go.state(),))
        # 入栈/出栈往返
        n0 = len(p.names())
        p.clear()
        p._refill()
        check('清空栈生效', len(p.names()) == 0)
        p.set_items(p.all)
        check('set_items 后候选恢复', len(p.all) > 0, '%d' % len(p.all))
    else:
        check('找到 ≥8 项的库以测超限', False)

    root.destroy()
    print()
    print('自检:', '全部 PASS ✅' if not FAILS else 'FAIL ❌ ' + ', '.join(FAILS))
    return 0 if not FAILS else 1


def _same_batch():
    """读 lib_delete.MAX_BATCH 而不 import 它（系统 312 没 olefile，import 会炸）。"""
    import re
    p = os.path.join(HERE, 'lib_delete.py')
    m = re.search(r'^MAX_BATCH\s*=\s*(\d+)', open(p, encoding='utf-8').read(), re.M)
    return int(m.group(1)) == DP.MAX_BATCH if m else None


if __name__ == '__main__':
    sys.exit(main())
