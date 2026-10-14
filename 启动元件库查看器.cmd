@echo off
setlocal
set "HERE=%~dp0"
set "EXE=%HERE%dist\元件库查看器.exe"
set "PYW=C:\Users\admin\AppData\Local\Programs\Python\Python312\pythonw.exe"
set "PY=C:\Users\admin\AppData\Local\Programs\Python\Python312\python.exe"

rem 优先启动打包好的 exe（单文件、带图标、不依赖源码）
if exist "%EXE%" (
    start "" "%EXE%"
    exit /b 0
)

rem 回退：没有 exe 就直接跑源码（开发态）
if not exist "%HERE%main.py" goto nomain
if exist "%PYW%" (
    start "" "%PYW%" "%HERE%main.py"
    exit /b 0
)
if exist "%PY%" (
    start "" "%PY%" "%HERE%main.py"
    exit /b 0
)
echo. & echo  [ERROR] Python 3.12 not found. & echo    expected: %PY% & echo.
pause & exit /b 1

:nomain
echo. & echo  [ERROR] neither exe nor main.py found in: & echo    %HERE% & echo.
echo    run  make_exe.py  to build the exe first. & echo.
pause & exit /b 1
