@echo off
rem Voice Secretary - stop app.py, link.py and the DSH web instance.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1" -Stop
