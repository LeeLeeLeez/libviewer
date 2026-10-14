# -*- coding: utf-8 -*-
"""C 方案端到端冒烟：验证 exe **自任 worker**（不依赖任何外部 .py）。

判据（dist 里那 4 个 .py 已删除，所以通过即证明 exe 自足）：
  1. `exe --worker --sub list --out F` → F 是可解析 JSON，含 16 个 (cat|kind) 键
  2. `exe --worker --sub build --out F` → F 里有「类别 N 个，符号 N，封装 N」且无乱码
  3. `exe --worker --sub sch --lib <不存在>` → 失败路径输出可读、无乱码
  4. GUI 模式（无参数）仍能建窗
"""
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import time

DIST = r'E:\AI_model\800_6_VPDB\LibViewer\dist'
SRC = r'E:\AI_model\800_6_VPDB\LibViewer'
EXE = os.path.join(DIST, '元件库查看器.exe')

# 🔴 伪造 frozen，让 worker_bridge 生成「exe 自任 worker」的 argv。
#    直接手拼 argv 容易漏参数 —— 第一版就漏了 `--mode`（见文件末尾教训）。
sys.frozen = True
sys.executable = EXE
sys._MEIPASS = r'C:\_fake_meipass_for_smoke'
sys.path.insert(0, SRC)
import worker_bridge as wb                                                # noqa: E402

RESULTS = []


def chk(name, cond, extra=''):
    cond = bool(cond)
    RESULTS.append((name, cond))
    print('%s %-46s%s' % ('[ OK ]' if cond else '[FAIL]', name,
                          '' if cond else ('  <-- ' + str(extra))))


def run_worker(sub, extra_args=(), timeout=600):
    """跑 exe 的 worker 子命令，返回 (rc, text, dt)。

    🔴 argv 由 worker_bridge 生成（它会自动补 `--mode <sub>`）——
       这样测的是**真实调用路径**，而不是手拼一条可能漏参数的命令。
       （教训：第一版手拼漏了 `--mode`，list 直接 rc=2。）
    """
    fd, out_file = tempfile.mkstemp(prefix='lvsmoke_', suffix='.txt')
    os.close(fd)
    argv = wb._build_argv(sub, extra_args, out_file)
    t0 = time.time()
    p = subprocess.run(argv, cwd=DIST, capture_output=True, timeout=timeout)
    dt = time.time() - t0
    text = ''
    try:
        with open(out_file, encoding='utf-8', errors='replace') as f:
            text = f.read()
    except OSError:
        pass
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass
    return p.returncode, text, dt


print('exe :', EXE)
chk('P0 exe 存在', os.path.isfile(EXE),
    '%.1f MB' % (os.path.getsize(EXE) / 1024 / 1024) if os.path.isfile(EXE) else '')
print('dist 内容:', sorted(os.listdir(DIST)))
print()

print('=== 0. 进程内路径（打包态首选，GUI 实际走这条）===')
# 伪造 frozen + 把 _vendor 放进 sys.path 让 olefile 可导入，
# 这样就能在**本进程**里验证 wb.run 的 inproc 分支（真 exe 里 olefile 在 PYZ 中）。
sys.path.insert(0, os.path.join(SRC, '_vendor'))
_inp = wb._inproc_available()
chk('0.1 inproc 可用（olefile + lib_reader 可导入）', _inp)
if _inp:
    t0 = time.time()
    rc0, out0, err0 = wb.run('list', timeout=300)
    dt0 = time.time() - t0
    print('  进程内 wb.run(list): rc=%s  用时 %.2fs  out=%d B'
          % (rc0, dt0, len(out0)))
    chk('0.2 进程内 rc=0', rc0 == 0, repr(out0[:200]))
    try:
        d0 = json.loads(out0)
        chk('0.3 进程内输出合法 JSON', len(d0) == 16, 'len=%d' % len(d0))
    except Exception as e:
        chk('0.3 进程内输出合法 JSON', False, repr(e))
    chk('0.4 进程内明显快于起 exe 子进程（<5s）', dt0 < 5.0,
        '%.1fs（子进程实测 ~32s）' % dt0)

print()
print('=== 1. worker 子命令：list（隔离备选路径，验证它仍可用）===')
rc, text, dt = run_worker('list')
print('  rc=%s  用时 %.1fs' % (rc, dt))
chk('1.1 rc == 0', rc == 0, 'rc=%s out=%r' % (rc, text[:300]))
try:
    data = json.loads(text)
    chk('1.2 输出是合法 JSON', True)
    chk('1.3 键数 == 16（8 类 × sch/pcb）', len(data) == 16,
        'len=%d' % len(data))
    oks = [k for k, v in data.items() if v.get('ok')]
    chk('1.4 至少一半库 ok', len(oks) >= 8, 'ok=%d/%d' % (len(oks), len(data)))
    s = data.get('01电阻|sch', {})
    chk('1.5 存中文键且名单非空',
        s.get('count', 0) > 100, 'count=%s' % s.get('count'))
    print('  样例: 01电阻|sch -> ok=%s count=%s' % (s.get('ok'), s.get('count')))
except Exception as e:
    chk('1.2 输出是合法 JSON', False, repr(e))

print()
print('=== 2. worker: build（重建缓存）===')
rc2, text2, dt2 = run_worker('build', timeout=900)
print('  rc=%s  用时 %.1fs' % (rc2, dt2))
chk('2.1 rc == 0', rc2 == 0, 'rc=%s out=%r' % (rc2, text2[:400]))
chk('2.2 无乱码（无 U+FFFD / 连续 ??）',
    ('\ufffd' not in text2) and ('??' not in text2), repr(text2[:250]))
chk('2.3 含中文「类别」「符号」',
    ('类别' in text2) and ('符号' in text2), repr(text2[-250:]))
_wrote = [ln.strip() for ln in text2.splitlines() if ln.strip().startswith('写入')]
_wp = _wrote[0] if _wrote else ''
chk('2.4 缓存写到 exe 旁（不是 _MEIPASS）',
    (DIST in _wp) and ('_MEI' not in _wp), _wp or '(输出里没有「写入」行)')
print('  尾部:', repr(text2[-180:]))

print()
print('=== 3. worker: sch 失败路径（不存在的库）===')
bad = os.path.join(DIST, '_no_such_中文.SchLib')
rc3, text3, dt3 = run_worker('sch', ['--lib', bad, '--names', 'A,B'])
print('  rc=%s' % rc3)
chk('3.1 rc != 0', rc3 != 0, 'rc=%s' % rc3)
chk('3.2 失败信息可读、无乱码',
    ('\ufffd' not in text3) and ('??' not in text3) and len(text3) > 0,
    repr(text3[:250]))

print()
print('=== 4. GUI 模式（无参数）仍能建窗 ===')
user32 = ctypes.windll.user32


def titles():
    out = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n > 0:
                b = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, b, n + 1)
                out.append(b.value)
        return True

    user32.EnumWindows(cb, 0)
    return out


before = set(titles())
p = subprocess.Popen([EXE], cwd=DIST,
                     creationflags=0x00000008 | 0x00000200, close_fds=True)
found = None
for i in range(25):
    time.sleep(0.8)
    if p.poll() is not None:
        break
    hit = [t for t in titles() if t not in before and '元件库' in t]
    if hit:
        found = hit[0]
        print('  建窗于 %.1fs，标题=%r' % ((i + 1) * 0.8, found))
        break
chk('4.1 GUI 建窗成功', found is not None,
    '进程存活=%s' % (p.poll() is None))
if p.poll() is None:
    subprocess.run(['taskkill', '/PID', str(p.pid), '/F'], capture_output=True)

print()
n_fail = sum(1 for _, ok in RESULTS if not ok)
print('RESULT: %s  (%d/%d)' % ('PASS' if n_fail == 0 else 'FAIL',
                               len(RESULTS) - n_fail, len(RESULTS)))
sys.exit(1 if n_fail else 0)
