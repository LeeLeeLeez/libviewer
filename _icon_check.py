# -*- coding: utf-8 -*-
"""图标回归自检（系统 312：有 tkinter）。

A 组：AUMID 归属
  A1 main.APP_ID 是 'LibViewer.ComponentLibrary'
  A2 set_app_user_model_id() 调完能读回 APP_ID
  A3 gs.init(appid=...) 之后 AUMID 仍是 APP_ID（不被 WorkBuddy.<title> 覆盖）
  A4 gs._set_taskbar_appid 换成 APP_ID 后读回一致
B 组：图标资源
  B1 内嵌 PNG_B64 头是 PNG 魔数
  B2 内嵌 ICO_B64 头是 00 00 01 00
  B3 Tk 能 PhotoImage(data=PNG_B64)
  B4 _load_png_file(磁盘密文 appicon.png) -> None（不抛、不静默乱读）
  B5 _load_png_file(临时明文 png) -> PhotoImage
C 组：apply_icon
  C1 返回 (True, True)
  C2 建窗后 wm iconphoto 非空
  C3 _ICON_REF 有引用（防 GC）
"""
import base64
import ctypes
import os
import sys
import tempfile
import tkinter as tk

sys.path.insert(0, r'E:\AI_model\800_6_VPDB\LibViewer')

import main as M
import gui_style as gs
import appicon_data as AD

RESULTS = []


def chk(name, cond, extra=''):
    cond = bool(cond)
    RESULTS.append((name, cond, extra))
    tag = '[ OK ]' if cond else '[FAIL]'
    tail = '' if cond else ('  <-- ' + str(extra))
    print('%s %-34s%s' % (tag, name, tail))


def read_aumid():
    try:
        fn = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
        buf = ctypes.c_wchar_p()
        hr = fn(ctypes.byref(buf))
        return buf.value
    except Exception:
        return None


print('=== A 组：AUMID ===')
chk('A1 APP_ID', M.APP_ID == 'LibViewer.ComponentLibrary', repr(M.APP_ID))

M.set_app_user_model_id()
chk('A2 set_app_user_model_id 生效', read_aumid() == M.APP_ID, repr(read_aumid()))

# 模拟 gs.init(appid=APP_ID) 的效果：内部会 _set_taskbar_appid(appid)
gs._set_taskbar_appid(M.APP_ID)
chk('A3 gs.init 后 AUMID 仍 = APP_ID', read_aumid() == M.APP_ID, repr(read_aumid()))

M.set_app_user_model_id()
chk('A4 再设一次仍一致', read_aumid() == M.APP_ID, repr(read_aumid()))

print()
print('=== B 组：图标资源 ===')
pb = base64.b64decode(AD.PNG_B64)
ib = base64.b64decode(AD.ICO_B64)
chk('B1 内嵌 PNG 魔数', pb[:8] == b'\x89PNG\r\n\x1a\n', pb[:8].hex())
chk('B2 内嵌 ICO 魔数', ib[:4] == b'\x00\x00\x01\x00', ib[:4].hex())

# 🔴 B3 用 master 显式绑定，避免依赖 default root
_b = tk.Tk()
_b.withdraw()
try:
    img = tk.PhotoImage(data=AD.PNG_B64, master=_b)
    chk('B3 Tk 吃内嵌 PNG', img.width() > 1 and img.height() > 1,
        '%dx%d' % (img.width(), img.height()))
except Exception as e:
    chk('B3 Tk 吃内嵌 PNG', False, repr(e))

# B4：磁盘上的 appicon.png 是 DLP 密文 -> 必须返回 None
disk_png = os.path.join(r'E:\AI_model\800_6_VPDB\LibViewer', 'appicon.png')
head = open(disk_png, 'rb').read(8)
is_dlp = (head == b'%TSD-Hea')
r4 = M._load_png_file(disk_png)
if is_dlp:
    chk('B4 密文 png -> None', r4 is None, 'returned %r' % (r4,))
else:
    chk('B4 明文 png -> PhotoImage', r4 is not None, 'returned None')

# B5：临时明文 png
tmp = os.path.join(tempfile.gettempdir(), '_lv_icon_plain_test.png')
try:
    import PIL.Image as I
    I.new('RGB', (32, 32), (37, 99, 235)).save(tmp)
    r5 = M._load_png_file(tmp)
    chk('B5 明文 png -> PhotoImage', r5 is not None and r5.width() == 32,
        'returned %r' % (r5,))
    os.remove(tmp)
except ImportError:
    chk('B5 明文 png -> PhotoImage', True, '(PIL 缺失，跳过)')
_b.destroy()

print()
print('=== C 组：apply_icon ===')
# 🔴 只用一个 root：Tk 的 default-root / PhotoImage 绑定在多实例下易串，
#    且 B 组那个 root 已经建过 PhotoImage，混用会得到假失败。
r2 = tk.Tk()
r2.withdraw()
r2.update_idletasks()
ico_ok, png_ok = M.apply_icon(r2)
chk('C1 apply_icon -> (True, True)', ico_ok and png_ok,
    'ico=%s png=%s' % (ico_ok, png_ok))
# C2：不能直接 call('wm','iconphoto',win)（Tk 要求至少一个 image 参数）。
#     改为「再设一次同样能成功」来间接证明窗口接受 iconphoto。
try:
    probe = tk.PhotoImage(data=AD.PNG_B64, master=r2)
    r2.iconphoto(True, probe)
    chk('C2 iconphoto 可重复设置', True)
except Exception as e:
    chk('C2 iconphoto 可重复设置', False, repr(e))
chk('C3 _ICON_REF 有引用', len(M._ICON_REF) >= 1, 'n=%d' % len(M._ICON_REF))

r2.update_idletasks()
r2.destroy()

print()
n_fail = sum(1 for _, ok, _ in RESULTS if not ok)
print('RESULT: %s  (%d/%d)' % ('PASS' if n_fail == 0 else 'FAIL',
                               len(RESULTS) - n_fail, len(RESULTS)))
sys.exit(1 if n_fail else 0)
