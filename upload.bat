@echo off
rem Reads your Auctionator scans, updates the website data and uploads it to GitHub Pages.
python "%~dp0tools\export.py" %*
pause
