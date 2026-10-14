# -*- coding: utf-8 -*-
"""编码回归自检：在**干净环境**（无 PYTHONUTF8）下验证 worker 输出是 UTF-8。

A 组：worker 自身
  A1 干净环境下 sys.stdout.encoding == 'utf-8'（reconfigure 生效）
  A2 干净环境下 sys.stderr.encoding == 'utf-8'
  A3 干净环境下 print 中文+emoji 的字节可被 utf-8 解出且**内容正确**
B 组：GUI 解码路径
  B1 download_page._utf8_env() 存在且注入两个键
  B2 用 _utf8_env() 跑 worker，stdout 中文正常
  B3 用 _utf8_env() 跑 worker，stderr 中文回溯正常
C 组：真实 worker 端到端
  C1 --mode list 输出可 json.loads（不因编码崩）
  C2 删除失败时的中文日志无乱码
"""
import json
import os
import subprocess
import sys

HERE = r'E:\AI_model\800_6_VPDB\LibViewer'
sys.path.insert(0, HERE)

WP = r'C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe'
WORKER = os.path.join(HERE, '_del_worker.py')

# 干净环境：剥掉 UTF-8 相关变量，复刻用户双击 .cmd 的场景
CLEAN = {k: v for k, v in os.environ.items()
         if k.upper() not in ('PYTHONUTF8', 'PYTHONIOENCODING')}

RESULTS = []


def chk(name, cond, extra=''):
    cond = bool(cond)
    RESULTS.append((name, cond))
    tag = '[ OK ]' if cond else '[FAIL]'
    print('%s %-38s%s' % (tag, name, '' if cond else ('  <-- ' + str(extra))))


print('干净环境 PYTHONUTF8 =', CLEAN.get('PYTHONUTF8'))
print()
print('=== A 组：worker 自身把 stdout/stderr 掰成 UTF-8 ===')
probe = ('import sys;'
         'print("OUT=" + str(sys.stdout.encoding));'
         'print("ERR=" + str(sys.stderr.encoding))')
r = subprocess.run([WP, '-c', probe], capture_output=True, text=True,
                   encoding='utf-8', errors='replace', env=CLEAN)
# 注意：-c 方式不经过 worker 的 reconfigure，所以这里探的是「裸 Python」
# 真正要探的是 worker 文件本身 → 用 worker 的 import 头
probe2 = (
    'import sys, runpy, io, contextlib;'
    'sys.argv=["x","--mode","list"];'
    'raise SystemExit(0)'
)
# 直接探 worker：在干净环境下导入它，然后看它的 stdout 编码
probe3 = (
    'import sys;'
    'sys.path.insert(0, r"%s");'
    'import importlib.util as u;'
    'spec=u.spec_from_file_location("w", r"%s");'
    'm=u.module_from_spec(spec);'
    'spec.loader.exec_module(m);'
    'print("OUT=" + str(sys.stdout.encoding));'
    'print("ERR=" + str(sys.stderr.encoding));'
    'print("\\u2705 \\u4e2d\\u6587OK")'
) % (HERE, WORKER)
r3 = subprocess.run([WP, '-c', probe3], capture_output=True, text=True,
                    encoding='utf-8', errors='replace', env=CLEAN)
out3 = r3.stdout or ''
chk('A1 worker 内 stdout.encoding == utf-8', 'OUT=utf-8' in out3, repr(out3[:120]))
chk('A2 worker 内 stderr.encoding == utf-8', 'ERR=utf-8' in out3, repr(out3[:120]))
chk('A3 worker 内中文+emoji 正确', '✅ 中文OK' in out3, repr(out3[:120]))

print()
print('=== B 组：GUI 的 _utf8_env ===')
try:
    import delete_page as DP
    has = hasattr(DP, '_utf8_env')
    chk('B1 _utf8_env 存在', has)
    if has:
        e = DP._utf8_env()
        ok = e.get('PYTHONUTF8') == '1' and e.get('PYTHONIOENCODING') == 'utf-8'
        chk('B1b 注入两键正确', ok, repr({k: e.get(k) for k in
                                          ('PYTHONUTF8', 'PYTHONIOENCODING')}))
        # B2：用它跑一个中文 print
        rc = subprocess.run([WP, '-c',
                             'print("  \u2705 \u53cc\u5411\u6838\u5bf9\u901a\u8fc7")'],
                            capture_output=True, text=True, encoding='utf-8',
                            errors='replace', env=e)
        chk('B2 经 _utf8_env 中文正常',
            '\u2705 \u53cc\u5411\u6838\u5bf9\u901a\u8fc7' in (rc.stdout or ''),
            repr(rc.stdout))
        # B3：中文异常回溯
        rc2 = subprocess.run([WP, '-c',
                              'raise RuntimeError("\u6587\u4ef6\u9501\u5b9a")'],
                             capture_output=True, text=True, encoding='utf-8',
                             errors='replace', env=e)
        chk('B3 经 _utf8_env 回溯中文正常',
            '\u6587\u4ef6\u9501\u5b9a' in (rc2.stderr or ''),
            repr((rc2.stderr or '')[-160:]))
except Exception as ex:
    chk('B1 导入 delete_page', False, repr(ex))

print()
print('=== C 组：真实 worker 端到端（干净环境 + _utf8_env）===')
try:
    import delete_page as DP
    env = DP._utf8_env()
except Exception:
    env = CLEAN

rc = subprocess.run([WP, WORKER, '--mode', 'list'], cwd=HERE,
                    capture_output=True, text=True, encoding='utf-8',
                    errors='replace', env=env, timeout=300)
try:
    data = json.loads(rc.stdout or '{}')
    chk('C1 --mode list 可 json.loads', isinstance(data, dict) and len(data) > 0,
        'keys=%d' % len(data))
    # 抽查一个 key 的中文 why 字段
    zh_ok = all(isinstance(v.get('name'), str) for v in list(data.values())[:3])
    chk('C1b 中文 name 字段正常', zh_ok)
except Exception as ex:
    chk('C1 --mode list 可 json.loads', False, repr(ex))

# C2：故意让删除失败，看中文日志
badlib = os.path.join(HERE, '_no_such_lib_中文.SchLib')
rc2 = subprocess.run([WP, WORKER, '--mode', 'sch', '--lib', badlib,
                      '--names', 'A,B'],
                     cwd=HERE, capture_output=True, text=True,
                     encoding='utf-8', errors='replace', env=env, timeout=120)
combined = (rc2.stdout or '') + (rc2.stderr or '')
# 乱码特征：出现 U+FFFD 替换符，或被替成 '?' 的连续问号
bad = ('\ufffd' in combined) or ('??' in combined)
chk('C2 失败路径无乱码', not bad, repr(combined[:200]))
chk('C2b 中文关键字「库」/「目标」可读',
    ('\u5e93' in combined) or ('\u76ee\u6807' in combined),
    repr(combined[:200]))

print()
print('=== D 组：Home 页 build_cache 路径（干净环境）===')
BUILDER = os.path.join(HERE, 'build_cache.py')
try:
    import main as M
    has_m = hasattr(M, '_utf8_env')
    chk('D1 main._utf8_env 存在', has_m)
    menv = M._utf8_env() if has_m else CLEAN
    chk('D1b main 注入两键正确',
        menv.get('PYTHONUTF8') == '1' and menv.get('PYTHONIOENCODING') == 'utf-8',
        repr({k: menv.get(k) for k in ('PYTHONUTF8', 'PYTHONIOENCODING')}))
except Exception as ex:
    chk('D1 main._utf8_env 存在', False, repr(ex))
    menv = CLEAN

# D2：build_cache.py 自身 reconfigure（干净环境下直接跑）
probe_bc = (
    'import sys; sys.path.insert(0, r"%s");'
    'import importlib.util as u;'
    'spec=u.spec_from_file_location("bc", r"%s");'
    'm=u.module_from_spec(spec);'
    'spec.loader.exec_module(m);'
    'print("OUT=" + str(sys.stdout.encoding))'
) % (HERE, BUILDER)
rbc = subprocess.run([WP, '-c', probe_bc], capture_output=True, text=True,
                     encoding='utf-8', errors='replace', env=CLEAN)
chk('D2 build_cache 内 stdout == utf-8',
    'OUT=utf-8' in (rbc.stdout or ''), repr(rbc.stdout[:120]))

# D3：真跑一次 build_cache，检查类别中文名不乱码
rbc2 = subprocess.run([WP, BUILDER], cwd=HERE, capture_output=True, text=True,
                      encoding='utf-8', errors='replace', env=menv, timeout=600)
out = rbc2.stdout or ''
bad3 = ('\ufffd' in out) or ('??' in out)
chk('D3 build_cache 输出无乱码', not bad3, repr(out[:200]))
# 抽查中文类别名可读（01电阻 / 02电容 / 07IC 等）
zh_hit = any(k in out for k in ('\u7535\u963b', '\u7535\u5bb9', '\u4e8c\u6781\u7ba1',
                                '\u4e09\u6781\u7ba1', '\u7535\u611f'))
chk('D3b 类别中文名可读', zh_hit, repr(out[:300]))
chk('D3c 结尾「类别 N 个」行可读',
    '\u7c7b\u522b' in out and '\u7b26\u53f7' in out, repr(out[-200:]))

print()
n_fail = sum(1 for _, ok in RESULTS if not ok)
print('RESULT: %s  (%d/%d)' % ('PASS' if n_fail == 0 else 'FAIL',
                               len(RESULTS) - n_fail, len(RESULTS)))
sys.exit(1 if n_fail else 0)
