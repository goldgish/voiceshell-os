@echo off
rem Voice Secretary - first-run setup wizard. Real logic lives in setup.py.
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 goto run_python

where py >nul 2>nul
if %errorlevel%==0 goto run_py

echo.
echo   [!] 没找到 Python。请先装 Python 3.11+，安装时勾上 "Add python.exe to PATH"
echo       https://www.python.org/downloads/
echo.
pause
exit /b 1

:run_python
python setup.py
goto done

:run_py
py -3 setup.py
goto done

:done
echo.
pause
