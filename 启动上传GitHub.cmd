@echo off
setlocal
rem ============================================================
rem  上传「元件库查看器」到 GitHub
rem
rem  双击打开上传工具：填 Token → 点「开始上传」。
rem  首次运行会自动建库；勾「打包 exe + 库文件」
rem  就会把 exe 与 AD 库正本作为 Release 附件（上传前做明文检验）。
rem
rem  Token: https://github.com/settings/tokens  (classic 勾 repo)
rem ============================================================
set "HERE=%~dp0"
set "APP=%HERE%upload_gui.py"
set "PYW=C:\Users\admin\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "PY=C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"

if not exist "%APP%" goto noapp

if exist "%PYW%" (
    start "" "%PYW%" "%APP%"
    exit /b 0
)
if exist "%PY%" (
    start "" "%PY%" "%APP%"
    exit /b 0
)

:nopy
echo.
echo  [错误] 没找到 Python 3.12
echo          期望位置: %PYW%
echo.
timeout /t 8 >nul
exit /b 1

:noapp
echo.
echo  [错误] 本目录下没有 upload_gui.py
echo          当前目录: %HERE%
echo.
timeout /t 8 >nul
exit /b 1
