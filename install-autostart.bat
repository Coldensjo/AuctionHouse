@echo off
rem Starts the auction house sync hidden every time you log in to Windows, and starts it now.
rem Its log is state\sync.log. Undo with uninstall-autostart.bat.
set "PYW="
for /f "delims=" %%p in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%p"
if not defined PYW (
	echo pythonw.exe was not found on PATH.
	pause
	exit /b 1
)
set "LNK=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Auction House Sync.lnk"
powershell -NoProfile -Command "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:LNK); $s.TargetPath = $env:PYW; $s.Arguments = '\"%~dp0tools\sync.py\"'; $s.WorkingDirectory = '%~dp0'; $s.Description = 'Uploads auction house scans'; $s.Save()"
start "" "%PYW%" "%~dp0tools\sync.py"
echo Auction House Sync now starts at login (and is running now). Log: %~dp0state\sync.log
pause
