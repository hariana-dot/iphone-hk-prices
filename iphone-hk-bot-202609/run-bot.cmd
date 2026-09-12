@echo off
REM iPhone HK stock bot (@iphone18hk_bot) launcher.
REM Manual double-click OR called from shell:startup at boot login.
REM Uses pythonw (no console window). Exits if the bot is already running
REM (twin pollers would 409-conflict on the Telegram token).
setlocal EnableExtensions
set "APP=C:\Users\boot\OpenCode\Projects\iphone-hk-prices\iphone-hk-bot-202609\app.py"
set "PYW=C:\Users\boot\AppData\Local\Programs\Python\Python311\pythonw.exe"
powershell -NoProfile -ExecutionPolicy Bypass -Command "if (Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*iphone-hk-bot-202609*app.py*' }) { exit 0 } else { exit 1 }"
if not errorlevel 1 exit /b 0
start "" "%PYW%" -u "%APP%"
exit /b 0
