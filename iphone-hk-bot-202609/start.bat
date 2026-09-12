@echo off
REM Run from this folder only. No cd needed.
pip install -r "%~dp0requirements.txt"
python -u "%~dp0app.py"
pause
