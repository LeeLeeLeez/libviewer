# -*- mode: python ; coding: utf-8 -*-
"""元件库查看器 (LibViewer) PyInstaller spec —— C 方案（真单文件便携）。

## 设计（2026-10-14 改造）

**exe 自己既是 GUI，也是 worker。**

GUI 里触及 olefile 的动作（读库名单 / 建缓存 / 删条目）原本交给
「装了 olefile 的 venv 解释器 + 旁边的 .py」。那样换设备就得连解释器一起搬，
且解释器路径是硬编码的 → 拷到别的机器直接失效。

现在改为：**把 olefile 打进 exe**，`main.py` 支持 `--worker <sub>` 子命令，
`worker_bridge.py` 在冻结态直接用 `sys.executable`（= exe 自己）起 worker。

🔴 windowed exe **没有 stdout**，所以 worker 输出走 `--out <临时文件>`，
   由父进程读回 —— 见 `worker_bridge.run()`。

→ 交付物只剩：`元件库查看器.exe`（+ 可选的 lib_cache.json），**零外部依赖**。

## 其他关键点

  1. `EXE(..., icon=ICO)` —— 图标**内嵌进 exe**，开始菜单/资源管理器/任务栏固定才认。
  2. `datas` 也带一份 .ico —— onefile 运行时解到 sys._MEIPASS，
     `main.apply_icon()` 经 RES 才找得到（appicon_data.py 的 base64 是主通道）。
  3. 🔴🔴 `HERE = os.path.dirname(os.path.abspath(SPEC))`。
     写 `os.path.dirname(SPECPATH)` 是**错的** —— SPECPATH 本身就是目录，再 dirname
     会落到上一级，datas 里的文件被 isfile() 判无而**静默跳过**（图标没进包且不报错）。
  4. `lib_reader` / `lib_delete` / `build_cache` / `_del_worker` / `olefile`
     现在**必须进包**（exe 要能自任 worker），不能再 exclude。
"""
import os

SPEC = os.path.abspath(SPEC)                    # noqa: F821  (spec 魔法变量)
HERE = os.path.dirname(SPEC)                    # 🔴 见上文第 3 点

ICO = os.path.join(HERE, 'appicon.ico')
PNG = os.path.join(HERE, 'appicon.png')

datas = []
if os.path.isfile(ICO):
    datas.append((ICO, '.'))
if os.path.isfile(PNG):
    datas.append((PNG, '.'))

# 🔴🔴 打包机（系统 Python 312）**没有 olefile**，而 exe 又需要它才能自任 worker。
#      所以把 olefile 单独放在 `_vendor/`（见 `_vendor/README.txt`），
#      经 pathex 让 PyInstaller 找得到 —— 这样不必污染系统 Python 环境。
VENDOR = os.path.join(HERE, '_vendor')

a = Analysis(
    [os.path.join(HERE, 'main.py')],
    pathex=[HERE, VENDOR],
    binaries=[],
    datas=datas,
    # 函数内 import 的模块，静态分析抓不全 → 显式列出
    hiddenimports=[
        'appicon_data',      # main.apply_icon 内 import
        'worker_bridge',     # main / delete_page import
        'plainize',          # HomePage.on_fetch 内 import
        'lib_reader',        # build_cache / _del_worker import
        'lib_delete',        # _del_worker import
        'build_cache',       # worker_cli 内 import
        '_del_worker',       # worker_cli 内 import（下划线开头必须显式声明）
        'olefile',           # lib_reader / lib_delete 顶层 import
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 只排真正用不到的重量级三方包
    excludes=[
        'matplotlib', 'numpy', 'scipy', 'pandas', 'PIL',
        'PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'wx',
        'pytest', 'setuptools', 'pip',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='元件库查看器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,                                  # upx 易触发杀软误报，关掉
    runtime_tmpdir=None,
    console=False,                              # 无控制台窗口（worker 走 --out 文件）
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICO if os.path.isfile(ICO) else None,  # 🔴 内嵌图标（三处生效）
)
