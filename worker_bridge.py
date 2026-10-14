# -*- coding: utf-8 -*-
"""worker_bridge.py —— 统一「源码态」与「打包态」两种 worker 启动方式。

## 为什么需要 worker
GUI 里一切触及 olefile 的动作（读库名单 / 建缓存 / 删条目）都交给**外部进程**，
因为 GUI 自己跑在系统 Python 312（有 tkinter、**无 olefile**）。

## 三种执行路径

| 形态 | 怎么执行 | 实测耗时 |
|---|---|---|
| ① 源码态（未打包） | venv 解释器 + 同目录 .py **子进程** | 0.58 s |
| ② **打包态·进程内**（**首选**） | 直接在 GUI 进程里 import 并调用 | **0.13 s** |
| ③ 打包态·子进程（备选/隔离用） | `exe --worker --sub X --out F` | **~32 s** ⚠ |

🔴🔴 **为什么打包态首选进程内**：PyInstaller **onefile** 每次启动都要把
   ~10 MB 解包到 `%TEMP%\_MEIxxxxxx\`，**退出时再全部删掉** —— 实测一个
   「启动即退出」的空 worker 就要 **~32 s**（解包 ~1.6 s + 清理 ~30 s；
   三种 stdout 捕获方式都一样）。GUI 里每点一次「读取库数据」「提交删除」
   都吃这个代价，无法接受。
   而打包后 olefile 就在本进程的包里（import 实测 **0.00 s**），直接调用即可。
   安全性由**工作线程**保证（delete_page / HomePage 都已在独立线程里调）。

🔴 子进程路径保留作「需要隔离执行」的备选，它有两个约束：
   · **windowed exe 没有 stdout**（`sys.stdout is None`）→ 输出写 `--out` 临时文件；
   · 慢（见上）。

## 对外只有一个入口

    rc, out, err = run('list')                      # 读库名单
    rc, out, err = run('sch', ['--lib', p, '--names', 'A,B'])
    rc, out, err = run('build')                     # 重建缓存

`sub` 取值：`'list' | 'sch' | 'pcb' | 'build'`
（前三个走 `_del_worker.py`，`build` 走 `build_cache.py`）
"""
import io
import os
import subprocess
import sys
import tempfile
import traceback

FROZEN = getattr(sys, 'frozen', False)
_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

# 源码态用的 venv 解释器（装了 olefile；GUI 自身不需要）
WORK_PY = r'C:\Users\admin\.workbuddy\binaries\python\envs\default\Scripts\python.exe'

# sub → 源码态脚本名
_SCRIPTS = {
    'list': '_del_worker.py',
    'sch': '_del_worker.py',
    'pcb': '_del_worker.py',
    'build': 'build_cache.py',
}

# 🔴 冻结时 APP_DIR = exe 所在目录（可写/持久）；否则 = 本文件所在目录（源码目录）
if FROZEN:
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
    RES_DIR = getattr(sys, '_MEIPASS', APP_DIR)
else:
    APP_DIR = RES_DIR = os.path.dirname(os.path.abspath(__file__))

HEALTH_HINT = (
    'worker 起不来：源码态需要 venv 解释器 %s；'
    '打包态应走 exe 自身的 --worker 子命令。' % WORK_PY
)


def utf8_env():
    """给子进程注入 UTF-8（本机默认 locale=cp936，不注入会乱码）。"""
    env = os.environ.copy()
    env['PYTHONUTF8'] = '1'
    env['PYTHONIOENCODING'] = 'utf-8'
    return env


def _mode_args(sub, args):
    """`--mode <sub>` 由这里统一补，调用方只传后续参数。"""
    a = list(args)
    if sub in ('list', 'sch', 'pcb'):
        a = ['--mode', sub] + a
    return a


def _inproc_available():
    """打包态下能否在**本进程内**直接跑 worker（olefile 已在包里）。"""
    if not FROZEN:
        return False
    try:
        import olefile          # noqa: F401
        import lib_reader       # noqa: F401
        return True
    except Exception:
        return False


def _run_inproc(sub, args):
    """在**本进程内**执行 worker —— 打包态的首选路径。

    🔴🔴 为什么不起 exe 子进程：
        PyInstaller **onefile** 每次启动都要把 ~10 MB 解包到临时目录
        （`%TEMP%\\_MEIxxxxxx\\`），**退出时再全部删掉**。实测一个
        「启动即退出」的空 worker 就要 **~32 s**
        （解包 ~1.6 s + 清理 ~30 s；三种 stdout 捕获方式都一样快不了）。
        GUI 里每点一次「读取库数据」「提交删除」都吃这个代价，无法接受。

        而打包后 olefile 就在本进程的包里（import 实测 0.00 s），
        直接调用即可。⚠️ 调用方已把它放在**工作线程**里跑
        （delete_page._reload_worker / HomePage.on_fetch），不会卡界面。

    返回 `(rc, stdout_text, stderr_text)`，与子进程路径同构。
    """
    import contextlib
    buf = io.StringIO()
    old_argv = sys.argv
    rc = 0
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        sys.argv = ['worker'] + _mode_args(sub, args)
        try:
            if sub == 'build':
                import build_cache as _m
                _m.main()
                rc = 0
            elif sub in ('list', 'sch', 'pcb'):
                import _del_worker as _m
                rc = int(_m.main() or 0)
            else:
                buf.write('未知 worker 子命令：%r\n' % (sub,))
                rc = 2
        except SystemExit as e:
            rc = int(e.code) if isinstance(e.code, int) else 1
        except BaseException:
            buf.write('\n' + traceback.format_exc())
            rc = 1
        finally:
            sys.argv = old_argv
    return rc, buf.getvalue(), ''


def _build_argv(sub, args, out_file=None):
    """拼出 worker 的完整 argv（仅子进程路径用）。

    `--mode <sub>` 由 `_mode_args` 自动补上，调用方只传后续参数
    （如 `run('sch', ['--lib', p, '--names', 'A,B'])`）。
    """
    a = _mode_args(sub, args)
    if FROZEN:
        argv = [sys.executable, '--worker', '--sub', sub]
        if out_file:
            argv += ['--out', out_file]
        return argv + a
    script = os.path.join(APP_DIR, _SCRIPTS[sub])
    return [WORK_PY, script] + a


def run(sub, args=(), timeout=300):
    """跑一次 worker，返回 `(returncode, stdout_text, stderr_text)`。

    🔴 无论哪种形态，返回的都是**已按 UTF-8 解好的文本**，调用方无需关心编码。
    """
    args = list(args)

    # 🔴 打包态首选**进程内**执行（理由见 _run_inproc：onefile 起子进程要 ~32 s）
    if _inproc_available():
        return _run_inproc(sub, args)

    if not FROZEN:
        p = subprocess.run(_build_argv(sub, args), cwd=APP_DIR,
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace',
                           timeout=timeout, env=utf8_env(),
                           creationflags=_NO_WINDOW)
        return p.returncode, (p.stdout or ''), (p.stderr or '')

    # ---- 打包态：windowed exe 无 stdout → 走临时文件 ----
    fd, out_file = tempfile.mkstemp(prefix='lvworker_', suffix='.txt')
    os.close(fd)
    try:
        p = subprocess.run(_build_argv(sub, args, out_file), cwd=APP_DIR,
                           capture_output=True, timeout=timeout,
                           env=utf8_env(), creationflags=_NO_WINDOW)
        text = ''
        try:
            with open(out_file, encoding='utf-8', errors='replace') as f:
                text = f.read()
        except OSError:
            pass
        err = ''
        if p.stderr:
            err = p.stderr.decode('utf-8', errors='replace')
        if not text and p.returncode != 0:
            text = err or ('worker 无输出（rc=%s）' % p.returncode)
        return p.returncode, text, err
    finally:
        try:
            os.remove(out_file)
        except OSError:
            pass


def worker_cli(argv):
    """`exe --worker ...` 的入口：把请求转发给包内的 worker 模块。

    argv 形如 `['--sub', 'sch', '--out', 'F', '--mode', 'sch', '--lib', ...]`。
    返回进程退出码。

    🔴 输出统一进 StringIO，最后一次性写 `--out` 指定的文件 ——
       windowed exe 的 `sys.stdout` 是 None，直接 print 会炸。
    """
    sub = None
    out_file = None
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == '--sub' and i + 1 < len(argv):
            sub = argv[i + 1]
            i += 2
        elif a == '--out' and i + 1 < len(argv):
            out_file = argv[i + 1]
            i += 2
        else:
            rest.append(a)
            i += 1

    buf = io.StringIO()
    save_out, save_err = sys.stdout, sys.stderr
    save_argv = list(sys.argv)
    rc = 0
    try:
        sys.stdout = sys.stderr = buf          # 收拢所有 print / traceback
        sys.argv = ['worker'] + rest
        if sub == 'ping':
            # 诊断用子命令：只测 exe 启动 + 各模块 import 耗时，不跑业务逻辑。
            # 用法： exe --worker --sub ping --out <file>
            import time as _t
            buf.write('pong\n')
            # 探测 frozen 下 importlib.reload 是否失败
            # （这就是「exe 点读取库数据不转明」的根因）
            try:
                import importlib as _il
                import plainize as _pz
                _il.reload(_pz)
                buf.write('reload plainize : OK\n')
            except Exception as _e:
                buf.write('reload plainize : FAIL %r\n' % (_e,))
            try:
                _il.reload(_pz)
            except Exception:
                pass
            for _name in ('olefile', 'lib_reader', 'lib_delete', '_del_worker',
                          'build_cache'):
                _t0 = _t.time()
                try:
                    __import__(_name)
                    buf.write('import %-12s %6.2fs\n' % (_name, _t.time() - _t0))
                except Exception as _e:
                    buf.write('import %-12s FAIL %r\n' % (_name, _e))
        elif sub == 'build':
            import build_cache as _m           # noqa: E402  （已打进包）
            _m.main()
            rc = 0
        else:
            import _del_worker as _m           # noqa: E402
            rc = int(_m.main() or 0)
    except SystemExit as e:                    # argparse 出错会抛这个
        rc = int(e.code) if isinstance(e.code, int) else 1
    except FileNotFoundError as e:
        buf.write('\n%s\n%r\n' % (HEALTH_HINT, e))
        rc = 1
    except BaseException:
        buf.write('\n' + traceback.format_exc())
        rc = 1
    finally:
        sys.stdout, sys.stderr = save_out, save_err
        sys.argv = save_argv
        if out_file:
            try:
                with open(out_file, 'w', encoding='utf-8', errors='replace') as f:
                    f.write(buf.getvalue())
            except OSError:
                pass
    return rc


def selfcheck():
    """轻量自检：返回 (ok, 说明列表)。供 GUI「关于/诊断」或命令行 -c 调用。"""
    msgs = []
    ok = True
    msgs.append('frozen      = %s' % FROZEN)
    msgs.append('APP_DIR     = %s' % APP_DIR)
    msgs.append('RES_DIR     = %s' % RES_DIR)
    if FROZEN:
        msgs.append('worker      = exe 自身 (%s)' % sys.executable)
    else:
        msgs.append('worker      = %s' % WORK_PY)
        if not os.path.isfile(WORK_PY):
            ok = False
            msgs.append('  ✘ venv 解释器不存在 —— 源码态读/删会失败')
    for sub, script in _SCRIPTS.items():
        if not FROZEN:
            p = os.path.join(APP_DIR, script)
            if not os.path.isfile(p):
                ok = False
                msgs.append('  ✘ 缺 %s（sub=%s）' % (script, sub))
    return ok, msgs


if __name__ == '__main__':
    _ok, _msgs = selfcheck()
    for _m in _msgs:
        print(_m)
    print('RESULT:', 'PASS' if _ok else 'FAIL')
    sys.exit(0 if _ok else 1)
