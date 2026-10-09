@echo off
rem Keeps running and uploads your auction data every time WoW saves it
rem (after a scan: click "Reload & Upload" in game, or /reload, log out or exit).
rem Close this window to stop. install-autostart.bat runs it hidden at every login instead.
python "%~dp0tools\sync.py"
pause
