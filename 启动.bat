@echo off
rem Voice Secretary - one-click launcher. Real logic lives in launcher.ps1.
chcp 65001 >nul
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher.ps1"
