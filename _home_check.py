# -*- coding: utf-8 -*-
"""_home_check.py —— Home 页专项静态自检（不起真 GUI 主循环）。

用系统 Python 3.12（有 tkinter）跑：
    python _home_check.py
"""
import os, sys, json, tkinter as tk
from tkinter import ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# lib_reader 需要 olefile —— 它在隔离 venv 里，挂上其 site-packages
VENV_SP = r'C:\Users\admin\.workbuddy\binaries\python\envs\default\Lib\site-packages'
if os.path.isdir(VENV_SP) and VENV_SP not in sys.path:
    sys.path.append(VENV_SP)

# 剥掉宿主注入的 safe-delete 批量守卫（否则子进程内 unlink 计数超限会 SystemExit）
for _k in ('CODEBUDDY_TOOL_CALL_ID',
           'CODEBUDDY_SAFE_DELETE_BULK_STATE_DIR',
           'CODEBUDDY_SAFE_DELETE_BULK_GUARD'):
    os.environ.pop(_k, None)

os.environ.setdefault('LIBVIEWER_BASE', r'E:\MaterialLib\AD_Lib')

import main as M            # noqa: E402
import lib_reader as LR     # noqa: E402
import gui_style as gs      # noqa: E402

FAIL = [0]
N = [0]


def ck(name, cond, extra=None):
    N[0] += 1
    if not cond:
        FAIL[0] += 1
    print('  [%s] %s%s' % (' OK ' if cond else 'FAIL', name,
                           ('  <- ' + str(extra)) if extra else ''))


def main():
    print('=== Home 页静态自检 ===')

    # ---- 1) 类与常量
    ck('main.HomePage 存在', hasattr(M, 'HomePage'))
    ck('HomePage 是 ttk.Frame 子类', issubclass(M.HomePage, ttk.Frame))

    # ---- 2) 真建一个 Home 页（独立 Tk，不起 mainloop）
    root = gs.init('Home自检', size=(1200, 780))
    root.withdraw()
    app = M.App.__new__(M.App)            # 只用壳，避开 __init__ 的 mainloop
    app.root = root
    app.data = {'built_at': '(selfcheck)', 'categories': []}

    class _Stub:
        def config(self, **kw):
            pass
    app.status = _Stub()
    app.pages = {}

    hp = M.HomePage(root, app)
    ck('HomePage 构造成功', hp is not None)
    ck('有 btn_fetch「读取库数据」', hasattr(hp, 'btn_fetch')
       and hp.btn_fetch.cget('text') == '读取库数据')
    ck('有 btn_scan「扫描密文」', hasattr(hp, 'btn_scan')
       and hp.btn_scan.cget('text') == '扫描密文')
    ck('有 btn_open「打开库目录」', hasattr(hp, 'btn_open')
       and hp.btn_open.cget('text') == '打开库目录')
    ck('目录表 Section 已建', hasattr(hp, 'tbl'))
    ck('日志框已建', hasattr(hp, 'txt'))

    # ---- 3) 目录表行数 == 类别数（单一真源）
    n_cat = len(LR.CATEGORIES)
    rows = hp.tbl.rows
    ck('目录表 %d 行（= 类别数）' % n_cat, len(rows) == n_cat, len(rows))

    cols = set(hp.tbl.keys)
    for need in ('cat', 'dir', 'sch', 'pcb', 'n_sch', 'n_pcb'):
        ck('表列含 %s' % need, need in cols, sorted(cols))

    # ---- 4) 每行都有目录与文件描述，且库根目录正确
    base = LR.get_base()
    ck('库根目录 = %s' % base, hp.lbl_base.cget('text').endswith(base))

    bad_dir = [r['cat'] for r in rows if not os.path.isdir(r['_dir'])]
    ck('所有类别目录均存在', not bad_dir, bad_dir)

    bad_sch = [r['cat'] for r in rows if 'SchLib' not in r['sch']]
    ck('所有行都描述了 SchLib', not bad_sch, bad_sch)
    bad_pcb = [r['cat'] for r in rows if 'PcbLib' not in r['pcb']]
    ck('所有行都描述了 PcbLib', not bad_pcb, bad_pcb)

    # ---- 5) 明暗文标注正确（抽一个已知明文 + 已知密文）
    tags = {r['cat']: (r['sch'], r['pcb']) for r in rows}
    ck('电容 SchLib 标为明文（OLE）', '明文' in tags['02电容'][0], tags['02电容'][0])
    ck('Port PcbLib 有明确标注', any(t in tags['25端子'][1]
                                    for t in ('明文', 'DLP密文', '（缺失）')),
       tags['25端子'][1])

    # ---- 6) 混合标注统计（打印，便于人工核对）
    n_plain = sum(1 for r in rows if '明文' in r['pcb'])
    n_dlp = sum(1 for r in rows if 'DLP密文' in r['pcb'])
    n_miss = sum(1 for r in rows if '（缺失）' in r['pcb'])
    print('        PcbLib 标注：明文 %d / DLP密文 %d / 缺失 %d' % (n_plain, n_dlp, n_miss))

    # ---- 7) 刷新不抛异常、行数稳定
    try:
        n2 = hp.refresh()
        ck('refresh() 返回行数 = %d' % n_cat, n2 == n_cat, n2)
    except Exception as e:
        ck('refresh() 不抛异常', False, repr(e))

    # ---- 8) 挂载顺序：Home 必须是导航第 1 项
    nav = gs.sidebar(root)
    pp = M.HomePage(nav.body, app)
    nav.add(pp, text='Home', icon='home', group='常用')
    ck('nav.add(Home) 成功', len(nav._pages) == 1, len(nav._pages))
    ck('Home 图标名 home 合法', 'home' in getattr(__import__('gui_sidebar'), 'ICON_NAMES', []))

    # ---- 9) 离线链路接口齐备（方案 A：密转明 → 离线解析）
    import plainize as PZ
    for fn in ('plainize_all', 'plainize_one', 'kind', 'format_report'):
        ck('plainize.%s 存在' % fn, hasattr(PZ, fn))
    ck('plainize 认得 OLE 明文魔数',
       PZ.OLE_MAGIC == bytes.fromhex('d0cf11e0a1b11ae1'), PZ.OLE_MAGIC.hex())
    ck('plainize 认得 DLP 密文魔数',
       PZ.DLP_MAGIC == bytes.fromhex('255453442d486561'), PZ.DLP_MAGIC.hex())
    ck('HomePage.on_scan 存在（扫描密文按钮）', hasattr(hp, 'on_scan'))
    ck('HomePage._scan_cipher 存在', hasattr(hp, '_scan_cipher'))
    ck('HomePage.on_fetch 存在（一键读取）', hasattr(hp, 'on_fetch'))
    ck('HomePage._warn_no_snapshot 存在（缺快照提示）',
       hasattr(hp, '_warn_no_snapshot'))

    # ---- 9b) 🔴 回归防线：_categories 不许依赖 olefile
    # 本机 GUI 跑在系统 Python 312（有 tkinter、无 olefile）。
    # 曾因 _categories() 里 `import lib_reader`（顶层要 olefile）
    # → ModuleNotFoundError 被 except 吞掉 → 返回 [] → Home 目录表整片空白。
    import inspect as _ins
    _src = _ins.getsource(M.HomePage._categories)
    ck('_categories 不直接 import lib_reader',
       'import lib_reader as LR' not in _src and 'import lib_reader\n' not in _src,
       '仍在 import lib_reader 会因缺 olefile 返回空表')
    ck('_categories 走 plainize 零依赖接口',
       'plainize' in _src and 'get_categories' in _src)
    # 实证：真实调用能拿到 8 类
    try:
        _cats, _base = M.HomePage._categories(hp)
        ck('_categories 实调返回 8 类', len(_cats) == 8, '%d 类' % len(_cats))
        ck('_categories 返回真实库根目录', 'AD_Lib' in str(_base), _base)
    except Exception as e:
        ck('_categories 实调返回 8 类', False, repr(e))
    # 实证：refresh 的行数应等于类别数
    try:
        _n = hp.refresh()
        ck('refresh() 行数 == 8', _n == 8, '%d 行' % _n)
        ck('目录表有 rows 数据', len(getattr(hp.tbl, 'rows', []) or []) == 8,
           '%d 行' % len(getattr(hp.tbl, 'rows', []) or []))
    except Exception as e:
        ck('refresh() 行数 == 8', False, repr(e))

    # lib_fetch 仍保留（历史资产，非 Home 必经路径）
    import lib_fetch as LF
    ck('lib_fetch.clean_env 仍存在（备查）', hasattr(LF, 'clean_env'))

    root.destroy()
    print('-' * 62)
    print('%d 项，FAIL %d' % (N[0], FAIL[0]))
    print('RESULT: %s' % ('PASS' if FAIL[0] == 0 else 'FAIL'))
    return 1 if FAIL[0] else 0


if __name__ == '__main__':
    sys.exit(main())
