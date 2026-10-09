@echo off
rem Stops starting the auction house sync at login, and stops it if it is running.
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Auction House Sync.lnk" 2>nul
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name like 'python%%'\" | Where-Object { $_.CommandLine -like '*auctionhouse*tools*sync.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Auction House Sync removed from startup and stopped.
pause
