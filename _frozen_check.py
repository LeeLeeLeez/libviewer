# -*- coding: utf-8 -*-
"""打包(frozen)路径 + worker 桥 回归自检。

C 方案（2026-10-14）后：
  · exe **自任 worker**（olefile 打进包）→ dist 里**不再需要**那 4 个 .py
  · worker 输出走 `--out` 临时文件（windowed exe 没有 stdout）

分两段：
  第一段（伪造 frozen，主进程）   —— 路径与 argv 断言
  第二段（子进程，真 frozen=False）—— 源码态端到端（wb.run 真跑一次）
用系统 Python 312 跑。
"""
import os
import subprocess
import sys

HERE_REAL = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(HERE_REAL, 'dist')
FAKE_MEI = os.path.join(os.environ.get('TEMP', r'C:\Temp'), '_fake_meipass_probe')
FAKE_EXE = os.path.join(DIST, '元件库查看器.exe')

RESULTS = []


def chk(name, cond, extra=''):
    cond = bool(cond)
    RESULTS.append((name, cond))
    print('%s %-46s%s' % ('[ OK ]' if cond else '[FAIL]', name,
                          '' if cond else ('  <-- ' + str(extra))))


print('=' * 70)
print('第一段：伪造 frozen（onefile 环境）')
print('=' * 70)
sys.frozen = True
sys.executable = FAKE_EXE
sys._MEIPASS = FAKE_MEI
sys.path.insert(0, HERE_REAL)

print('  frozen=%s' % sys.frozen)
print('  exe   =%s' % FAKE_EXE)
print('  _MEI  =%s' % FAKE_MEI)
print()

import main as M                                                          # noqa: E402
import delete_page as DP                                                  # noqa: E402
import worker_bridge as wb                                                # noqa: E402

print('=== A 组：main 路径 ===')
chk('A1 HERE == exe 所在目录(dist)', M.HERE == DIST, M.HERE)
chk('A2 HERE != _MEIPASS', M.HERE != FAKE_MEI, M.HERE)
chk('A3 RES == _MEIPASS', M.RES == FAKE_MEI, M.RES)
chk('A4 CACHE 落在 exe 旁',
    M.CACHE == os.path.join(DIST, 'lib_cache.json'), M.CACHE)
chk('A5 ICON_ICO 取打包资源',
    M.ICON_ICO == os.path.join(FAKE_MEI, 'appicon.ico'), M.ICON_ICO)
chk('A6 APP_ID 未变', M.APP_ID == 'LibViewer.ComponentLibrary')
_main_src = open(os.path.join(HERE_REAL, 'main.py'), encoding='utf-8').read()
chk('A7 main 已支持 --worker 分发', "'--worker'" in _main_src)
# 🔴 frozen 下 importlib.reload 会抛异常 → 必须有 frozen 保护
#   （否则 on_fetch 一开始就中断，表现为「exe 不转明」）
# 🔴 要检查**每一处** reload（main.py 里有两处：
#   on_fetch 与 _scan_cipher）—— 用 find 只查第一个会漏掉另一处。
_bad = []
_pos = 0
while True:
    _i = _main_src.find('importlib.reload(plainize)', _pos)
    if _i < 0:
        break
    _guard = _main_src[max(0, _i - 500):_i]
    if "getattr(sys, 'frozen', False)" not in _guard:
        _bad.append(_main_src[:_i].count('\n') + 1)
    _pos = _i + 1
_n_reload = _main_src.count('importlib.reload(plainize)')
chk('A8 %d 处 reload 均有 frozen 保护' % _n_reload,
    _n_reload >= 2 and not _bad,
    '无保护的行: %s' % _bad)

print()
print('=== B 组：worker_bridge 路径 ===')
chk('B1 wb.FROZEN is True', wb.FROZEN is True, wb.FROZEN)
chk('B2 wb.APP_DIR == dist', wb.APP_DIR == DIST, wb.APP_DIR)
chk('B3 wb.RES_DIR == _MEIPASS', wb.RES_DIR == FAKE_MEI, wb.RES_DIR)
chk('B4 delete_page.HERE == dist', DP.HERE == DIST, DP.HERE)

print()
print('=== C 组：worker argv 拼接（frozen 态）===')
a1 = wb._build_argv('sch', ['--lib', 'X.SchLib', '--names', 'A,B'], 'OUT')
want1 = [FAKE_EXE, '--worker', '--sub', 'sch', '--out', 'OUT',
         '--mode', 'sch', '--lib', 'X.SchLib', '--names', 'A,B']
chk('C1 sch argv 正确', a1 == want1, a1)
a2 = wb._build_argv('list', [], 'OUT')
want2 = [FAKE_EXE, '--worker', '--sub', 'list', '--out', 'OUT', '--mode', 'list']
chk('C2 list argv 正确', a2 == want2, a2)
a3 = wb._build_argv('build', ['--only', 'ic'], 'OUT')
want3 = [FAKE_EXE, '--worker', '--sub', 'build', '--out', 'OUT', '--only', 'ic']
chk('C3 build argv 正确（不补 --mode）', a3 == want3, a3)
chk('C4 frozen 态 worker = exe 自身',
    a1[0] == FAKE_EXE and wb.WORK_PY not in a1, a1[0])

print()
print('=== D 组：C 方案后 dist 只需 exe（+可选 json）===')
exe_p = os.path.join(DIST, '元件库查看器.exe')
chk('D1 exe 存在', os.path.isfile(exe_p), exe_p)
for gone in ['_del_worker.py', 'build_cache.py', 'lib_reader.py',
             'lib_delete.py']:
    chk('D2 %-16s 不再复制到 dist' % gone,
        not os.path.isfile(os.path.join(DIST, gone)),
        '仍存在 → 删掉，否则会误以为还要靠外部脚本')

print()
print('=' * 70)
print('第二段：子进程（真 frozen=False）—— 源码态端到端')
print('=' * 70)
PY = r'C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe'
code = r'''
import sys, json
sys.path.insert(0, r"%s")
import worker_bridge as wb
print("FROZEN=" + str(wb.FROZEN))
print("APP_DIR=" + wb.APP_DIR)
print("RES_DIR=" + wb.RES_DIR)
print("ARGV_LIST=" + repr(wb._build_argv("list", [])))
rc, out, err = wb.run("list", timeout=300)
print("RC=" + str(rc))
try:
    d = json.loads(out)
    print("KEYS=" + str(len(d)))
    print("SAMPLE=" + repr(sorted(d.keys())[:3]))
except Exception as e:
    print("JSONFAIL=" + repr(e))
    print("RAW=" + repr(out[:300]))
''' % HERE_REAL
r = subprocess.run([PY, '-c', code], capture_output=True, text=True,
                   encoding='utf-8', errors='replace',
                   env={**os.environ, 'PYTHONUTF8': '1'}, timeout=900)
out = r.stdout or ''
print(out.strip() or '(无输出)')
if (r.stderr or '').strip():
    print('STDERR:', r.stderr.strip()[:400])
print()

chk('E1 源码态 FROZEN=False', 'FROZEN=False' in out, repr(out[:120]))
chk('E2 源码态 APP_DIR == 源码目录', ('APP_DIR=' + HERE_REAL) in out)
chk('E3 源码态 argv 用 venv 解释器 + _del_worker.py',
    'ARGV_LIST=' in out and '_del_worker.py' in out
    and 'envs' in out and 'Scripts' in out, repr(out[:400]))
chk('E4 源码态 wb.run(list) rc=0', 'RC=0' in out, repr(out[-300:]))
chk('E5 源码态返回可解析 JSON',
    'KEYS=' in out and 'JSONFAIL' not in out, repr(out[-400:]))

n_fail = sum(1 for _, ok in RESULTS if not ok)
print()
print('RESULT: %s  (%d/%d)' % ('PASS' if n_fail == 0 else 'FAIL',
                               len(RESULTS) - n_fail, len(RESULTS)))
sys.exit(1 if n_fail else 0)
