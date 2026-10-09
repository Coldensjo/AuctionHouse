@echo off
rem Reads your Auctionator scans, updates the website data and uploads it once.
python "%~dp0tools\sync.py" --once
pause
