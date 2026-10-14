# -*- coding: utf-8 -*-
"""本页端到端自检（系统 312 + tkinter）：
① 类别来回切换不炸（刚修的 bug）；
② 真实走一遍「入栈 → 预演 → 提交」，在**副本**上验证，并把结果逐项核对。
"""
import os
import sys
import time
import json
import shutil
import tkinter as tk

HERE = r'E:\AI_model\800_6_VPDB\LibViewer'
TMP = r'C:\Users\admin\AppData\Roaming\MaterialLibManager\output\adlib\temp'
sys.path.insert(0, HERE)
os.chdir(HERE)

import gui_style as gs            # noqa: E402
import delete_page as DP          # noqa: E402

FAILS = []


def check(name, cond, extra=''):
    print('  [%s] %-48s %s' % ('OK' if cond else '!!', name, extra))
    if not cond:
        FAILS.append(name)


class FakeApp(object):
    def __init__(self, root):
        self.root = root
        with open(os.path.join(HERE, 'lib_cache.json'), encoding='utf-8') as f:
            self.data = json.load(f)


def wait(root, page, secs=120):
    t0 = time.time()
    while page._busy and time.time() - t0 < secs:
        root.update()
        time.sleep(0.1)
    root.update()


def main():
    print('=== 类别切换 + 端到端提交自检 ===')
    root = gs.init('自检2', size=(1280, 860))
    root.withdraw()
    page = DP.DeletePage(root, FakeApp(root))
    page.pack(fill='both', expand=True)
    root.update()
    wait(root, page)

    # ── ① 类别来回切换（原 bug 场景）
    kind = 'sch'
    tv = page.trees[kind]
    kids = tv.get_children()
    check('sch 树 ≥2 行可切换', len(kids) >= 2, '%d' % len(kids))
    ok_switch = True
    try:
        for i in range(len(kids)):
            tv.selection_set(kids[i])
            root.update()
        for i in range(len(kids) - 1, -1, -1):
            tv.selection_set(kids[i])
            root.update()
    except Exception as ex:
        ok_switch = False
        print('     EXC:', repr(ex))
    check('全部类别来回切换不炸', ok_switch)
    check('切换后 sch 只剩 1 个面板', len([k for k in page.panels if k[1] == kind]) == 1,
          str(sorted(k for k in page.panels if k[1] == kind)))
    check('另一 tab 的面板互不影响',
          len([k for k in page.panels if k[1] != kind]) <= 1,
          str(sorted(k for k in page.panels if k[1] != kind)))
    check('当前面板仍可用', page._cur in page.panels)

    # ── ② 端到端：把 diode 库指向副本，走真实提交
    print()
    print('--- 端到端提交（副本）---')
    src = r'E:\MaterialLib\AD_Lib\diode\Diode.SchLib'
    dst = os.path.join(TMP, '_e2e_del.SchLib')
    shutil.copy2(src, dst)
    before = _names(dst)

    # 把副本「冒充」成 diode 的正本路径
    key = '05二极管|sch'
    page._libs[key]['path'] = dst
    tv = page.trees['sch']
    for iid in tv.get_children():
        if iid == '05二极管':
            tv.selection_set(iid)
            break
    root.update()
    p = page.panels[('05二极管', 'sch')]
    pick = before[:3]
    p.stack = list(pick)
    p._refill()
    page._cur = ('05二极管', 'sch')
    page._status()
    root.update()
    check('待删栈 = 3', len(p.names()) == 3, str(p.names()))
    check('提交按钮可用', 'disabled' not in page.btn_go.state())

    # 预演
    page.submit(dry=True)
    wait(root, page)
    check('预演后文件未变', _names(dst) == before, '%d 项' % len(_names(dst)))

    # 真提交
    page.submit(dry=False)
    # 会弹 askyesno —— 无头环境直接 monkeypatch 掉
    wait(root, page)
    after = _names(dst)
    gone = [x for x in before if x not in set(after)]
    extra = [x for x in after if x not in set(before)]
    check('目标 3 个全部消失', gone == pick, str(gone))
    check('无多余消失', not extra, str(extra))
    check('计数 -3', len(after) == len(before) - 3,
          '%d -> %d' % (len(before), len(after)))
    bk = [x for x in os.listdir(TMP) if x.startswith('_e2e_del.SchLib.bak_del_')]
    check('已生成备份', bool(bk), str(bk[:1]))
    check('界面候选已同步', len(page._libs[key]['items']) == len(after),
          '%d' % len(page._libs[key]['items']))

    # 超限：入栈 7 个后按钮应被禁用
    p.set_items(page._libs[key]['items'])
    p.stack = page._libs[key]['items'][:7]
    p._refill()
    page._status()
    root.update()
    check('超限(7)时按钮禁用', 'disabled' in page.btn_go.state(),
          'state=%s' % (page.btn_go.state(),))

    root.destroy()
    print()
    print('自检:', '全部 PASS ✅' if not FAILS else 'FAIL ❌ ' + ', '.join(FAILS))
    return 0 if not FAILS else 1


def _names(path):
    import subprocess
    out = subprocess.run(
        [DP.WORK_PY, '-c',
         'import sys,json;sys.path.insert(0,r"%s");import lib_reader as L;'
         'print(json.dumps([x["name"] for x in L.read_schlib({"path":r"%s","zip_entry":None})]))'
         % (HERE, path)],
        capture_output=True, text=True, encoding='utf-8')
    return json.loads(out.stdout)


if __name__ == '__main__':
    # 无头环境下 askyesno 直接放行
    import tkinter.messagebox as mb
    mb.askyesno = lambda *a, **k: True
    sys.exit(main())
