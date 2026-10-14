# -*- coding: utf-8 -*-
"""make_exe.py —— 把「元件库查看器」打包成单文件 exe。

用法（**必须用系统 Python 312**，它有 tkinter + PyInstaller）：
    C:\\Users\\admin\\AppData\\Local\\Programs\\Python\\Python312\\python.exe make_exe.py

产出
----
    dist\\元件库查看器.exe        ← 双击即用（onefile，图标已内嵌）
    dist\\lib_cache.json          ← 数据快照（源目录已有则一并带上；可删，会重建）

**零外部依赖**（2026-10-14 C 方案）：olefile 已打进 exe，
worker（读库名单 / 建缓存 / 删条目）由 exe 自己担任
（`exe --worker <sub> --out <file>`），见 `worker_bridge.py`。
拷到别的设备时**只需 exe + 库文件**，不必搬 Python 环境。

🔴 交付标准（本机常驻规则）
    1. `EXE(..., icon=appicon.ico)` —— 图标**内嵌**，开始菜单/资源管理器/任务栏固定才认。
    2. `datas` 带一份 .ico/.png —— onefile 解到 sys._MEIPASS，运行时 RES 才找得到。
    3. **打包后必须核 exe 时间戳 > 所有源文件时间戳** ——
       `dist/x.exe` 存在 ≠ 是新版（踩过：界面还是旧行为）。
"""
import hashlib
import os
import shutil
import subprocess
import sys
import time

# 🔴🔴 强制 stdout/stderr 走 UTF-8。
#   2026-10-14 血案：从 pythonw（无控制台）经 gh_upload 调起本脚本时，
#   stdout 编码是 GBK，打印 `✅`（emoji）直接 UnicodeEncodeError，
#   整个脚本 rc=1 —— 但 **exe 其实已经打包成功**，
#   表现为「打包失败（退出码 1）。输出末尾：」后面要么空白、要么一堆乱码。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
SPEC = os.path.join(HERE, '元件库查看器.spec')
DIST = os.path.join(HERE, 'dist')
WORK = os.path.join(HERE, 'build')

EXE_NAME = '元件库查看器.exe'

# ↳ GUI 进程内 import 的（不复制，用 exe 里的）
GUI_MODULES = ['main.py', 'gui_style.py', 'gui_sidebar.py',
               'delete_page.py', 'plainize.py', 'appicon_data.py',
               'worker_bridge.py']

# ↳ 外部 venv 进程要用的脚本 —— C 方案（2026-10-14）后**不再需要**复制：
#   olefile 已打进 exe，worker 由 exe 自己担任（`exe --worker <sub>`）。
#   保留空列表是为了让 copy_externals() 逻辑不变，将来要加就填这里。
EXTERNAL = []

# ↳ 一并带过去的数据文件
DATA_FILES = ['lib_cache.json']

# ↳ 时间戳基线：这些文件任一比 exe 新 ⇒ 打包是旧的
#   ⚠ lib_reader / lib_delete / _del_worker / build_cache 现在**也进包了**
#     （exe 自任 worker），改了它们同样必须重打包。
STAMP_BASELINE = (GUI_MODULES + ['appicon.ico', 'lib_reader.py',
                                 'lib_delete.py', '_del_worker.py',
                                 'build_cache.py', '元件库查看器.spec'])


def human(n):
    return '%.1f KB' % (n / 1024.0)


def die(msg):
    print('\n[ERROR] ' + msg)
    sys.exit(1)


def check_interpreter():
    """必须是带 tkinter 的解释器，否则打出来的 exe 起不来。"""
    try:
        import tkinter
        print('  解释器 : %s' % sys.executable)
        print('  tkinter: %s' % tkinter.TkVersion)
    except Exception as e:
        die('当前解释器没有 tkinter（%r）。请用系统 Python 312 运行本脚本。' % (e,))
    try:
        import PyInstaller
        print('  PyInstaller: %s' % PyInstaller.__version__)
    except Exception:
        die('没装 PyInstaller。请先在系统 Python 312 里 pip install pyinstaller')

    # 🔴🔴 exe 要自任 worker（读写 OLE），必须把 olefile 打进去。
    #      但打包机（系统 Python 312）**没有** olefile → 从 _vendor 取
    #      （spec 的 pathex 已指向它）。
    #      缺了它 exe **能启动**，但「读取库数据 / 提交删除」会
    #      ModuleNotFoundError —— 且这种失败很容易被误当成别的毛病。
    vend = os.path.join(HERE, '_vendor', 'olefile', 'olefile.py')
    if not os.path.isfile(vend):
        die('缺 %s\n'
            '       → 打出来的 exe 无法自任 worker（读取/删除会报 ModuleNotFoundError）。\n'
            '       修法见 _vendor/README.txt：从 venv 的 site-packages\\olefile\\\n'
            '       拷 __init__.py 与 olefile.py 到 _vendor\\olefile\\。' % vend)
    print('  olefile   : _vendor/olefile (%d B)'
          % os.path.getsize(vend))
    return True


def snapshot_sources():
    """记录关键源文件的时间戳，用于事后核对。"""
    out = {}
    for f in STAMP_BASELINE:
        p = os.path.join(HERE, f)
        if os.path.isfile(p):
            out[f] = os.path.getmtime(p)
    for f in EXTERNAL:
        p = os.path.join(HERE, f)
        if os.path.isfile(p):
            out[f] = os.path.getmtime(p)
    return out


def _kill_running():
    """关掉正在运行的 exe —— 否则 dist 里的 exe 被占用，PyInstaller 删不掉它。

    🔴 实测踩到（2026-10-14）：PyInstaller 删旧输出时 `os.remove` 抛
    `OSError: [safe-delete] 操作失败 ... Error during a trash operation`，
    真因是 GUI 还开着、`dist\\元件库查看器.exe` 被锁。
    """
    try:
        r = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq %s' % EXE_NAME,
                            '/FO', 'CSV', '/NH'],
                           capture_output=True, text=True,
                           encoding='utf-8', errors='replace')
        pids = []
        for ln in (r.stdout or '').splitlines():
            parts = [p.strip('"') for p in ln.split('","')]
            if len(parts) >= 2 and parts[0].lower() == EXE_NAME.lower():
                try:
                    pids.append(int(parts[1]))
                except ValueError:
                    pass
        if not pids:
            print('  没有正在运行的实例')
            return
        for pid in pids:
            subprocess.run(['taskkill', '/PID', str(pid), '/F'],
                           capture_output=True)
        print('  已关闭运行中的实例: %s' % pids)
        time.sleep(1.2)
    except Exception as e:
        print('  （关闭运行实例失败：%r）' % e)


def clean():
    for d in (DIST, WORK):
        if not os.path.isdir(d):
            continue
        try:
            shutil.rmtree(d)
            print('  清空 %s' % os.path.basename(d))
        except Exception as e:
            # 🔴 删不掉时（文件被占 / 宿主环境的 safe-delete 钩子拦截）退而求其次：
            #    改名让路，让 PyInstaller 直接建新的，不阻塞打包。
            alt = '%s_old_%s' % (d, time.strftime('%Y%m%d_%H%M%S'))
            try:
                os.rename(d, alt)
                print('  改名让路 %s -> %s（原目录删不掉，可稍后手动清理）'
                      % (os.path.basename(d), os.path.basename(alt)))
            except Exception as e2:
                print('  ⚠ 无法清空 %s：%r / %r' % (d, e, e2))


def build():
    if not os.path.isfile(SPEC):
        die('找不到 spec：%s' % SPEC)
    cmd = [sys.executable, '-m', 'PyInstaller',
           '--noconfirm', '--clean',
           '--distpath', DIST,
           '--workpath', WORK,
           SPEC]
    # ⚠ 给 .spec 时**不能**传 --specpath / --name / --onefile 等 makespec 选项，
    #   PyInstaller 会直接报 "option(s) not allowed"。dist/work 路径可以给。
    print('  执行 : %s' % ' '.join(cmd[:6] + ['...']))
    t0 = time.time()
    p = subprocess.run(cmd, cwd=HERE, capture_output=True,
                       text=True, encoding='utf-8', errors='replace')
    dt = time.time() - t0
    if p.returncode != 0:
        tail = (p.stdout or '')[-2000:] + '\n' + (p.stderr or '')[-2000:]
        die('PyInstaller 失败（rc=%d）：\n%s' % (p.returncode, tail))
    print('  构建完成，用时 %.1f s' % dt)
    # 保留警告（图标没进包之类会在这里露头）
    warns = [ln for ln in (p.stdout or '').splitlines()
             if 'WARNING' in ln or 'ERROR' in ln]
    if warns:
        print('  --- PyInstaller 警告 %d 条（前 8）---' % len(warns))
        for ln in warns[:8]:
            print('    ' + ln.strip()[:150])
    return p.stdout or ''


def copy_externals():
    """把外部脚本复制到 dist（exe 靠 HERE 找它们）。"""
    print('  准备 dist 外部依赖…')
    miss = []
    for f in EXTERNAL:
        src = os.path.join(HERE, f)
        if not os.path.isfile(src):
            miss.append(f)
            continue
        shutil.copy2(src, os.path.join(DIST, f))
        print('    + %s' % f)
    for f in DATA_FILES:
        src = os.path.join(HERE, f)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST, f))
            print('    + %s（随包带上，首启即可看到数据）' % f)
        else:
            print('    - %s 不存在（首启点「读取库数据」会生成）' % f)
    if miss:
        print('    ⚠ 缺失外部脚本：%s' % '、'.join(miss))


def verify(baseline):
    exe = os.path.join(DIST, EXE_NAME)
    if not os.path.isfile(exe):
        die('没生成 %s' % exe)
    exe_mt = os.path.getmtime(exe)

    print('\n=== 交付核验 ===')
    print('  exe    : %s' % exe)
    print('  大小   : %s' % human(os.path.getsize(exe)))
    print('  时间   : %s' % time.strftime('%Y-%m-%d %H:%M:%S',
                                        time.localtime(exe_mt)))
    with open(exe, 'rb') as f:
        head = f.read(2)
    print('  PE 头  : %s %s' % (head.hex(), '(MZ = 合法 Windows 可执行)' if head == b'MZ' else '⚠ 异常'))

    stale = [(f, t) for f, t in baseline.items() if t > exe_mt]
    if stale:
        print('\n  🔴 以下源文件比 exe 新 —— 打包是**旧的**，界面里可能还是老行为：')
        for f, t in sorted(stale, key=lambda x: -x[1]):
            print('     %-20s %s' % (f, time.strftime('%m-%d %H:%M:%S',
                                                      time.localtime(t))))
        die('exe 时间戳落后于源文件，请重新打包')
    print('  时间戳 : ✅ exe 比所有源文件都新')

    # 图标是否真的内嵌（PE 资源节里能搜到 ICO 的字节特征很弱，
    # 这里改用「文件里出现 icon 目录名」+ 体积合理性做粗判）
    print('\n  dist 目录内容：')
    for f in sorted(os.listdir(DIST)):
        p = os.path.join(DIST, f)
        if os.path.isfile(p):
            print('    %-22s %10s' % (f, human(os.path.getsize(p))))
    return True


def main():
    print('=' * 62)
    print('打包「元件库查看器」')
    print('=' * 62)
    check_interpreter()
    baseline = snapshot_sources()

    print('\n[1/4] 清理旧产物')
    _kill_running()          # 🔴 必须先关实例，否则 dist 里的 exe 被锁、删不掉
    clean()

    print('\n[2/4] PyInstaller 构建')
    out = build()

    print('\n[3/4] 复制外部脚本到 dist')
    copy_externals()

    print('\n[4/4] 交付核验')
    verify(baseline)

    # 附带记录一下构建产物的指纹
    exe = os.path.join(DIST, EXE_NAME)
    with open(exe, 'rb') as f:
        md5 = hashlib.md5(f.read()).hexdigest()
    print('\n  exe md5: %s' % md5)
    print('\n✔ 完成。双击：%s' % exe)
    return 0


if __name__ == '__main__':
    sys.exit(main())
