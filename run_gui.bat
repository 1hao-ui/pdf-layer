@echo off
rem 手写体生成器 · 图形界面启动器（在 _refs\pdf-layer 下双击本文件即可）
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 gui.py
) else (
  python gui.py
)
if errorlevel 1 pause
