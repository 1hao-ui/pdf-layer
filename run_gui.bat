@echo off
cd /d "%~dp0"
rem PDF-Layer GUI launcher. Double-click this file to open the console.
rem A Python with tkinter is required. Prefer the system Python 3.13
rem (the WorkBuddy sandbox Python has no tkinter), then fall back to PATH.
set "PY="
if exist "%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe" set "PY=%LOCALAPPDATA%\Programs\Python\Python313\pythonw.exe"
if not defined PY (
  where pythonw.exe >nul 2>&1 && set "PY=pythonw.exe"
)
if not defined PY (
  where python.exe >nul 2>&1 && set "PY=python.exe"
)
if not defined PY (
  echo [pdf-layer] Python 3 with tkinter not found. Please install Python 3 first.
  pause
  exit /b 1
)
start "" "%PY%" gui.py
