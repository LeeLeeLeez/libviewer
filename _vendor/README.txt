olefile — 打包用的第三方依赖（不污染系统 Python）
================================================

来源 : venv 的 site-packages\olefile（edition 0.47）
为何放这里: 打包机（系统 Python 312）**没有** olefile，
          而 exe 需要它来自任 worker（读写 OLE）。
          放在 _vendor 让 PyInstaller 经 pathex 找到，
          又不影响源码态（venv 自己有 olefile）。

升级方法:
  copy "<venv>\Lib\site-packages\olefile\__init__.py"  _vendor\olefile  copy "<venv>\Lib\site-packages\olefile\olefile.py"   _vendor\olefile  然后重新打包（make_exe.py）
