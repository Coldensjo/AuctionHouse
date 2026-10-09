@echo off
rem Keeps running and uploads automatically every time WoW saves new Auctionator data
rem (on /reload, logout or exit). Close the window to stop.
python "%~dp0tools\export.py" --watch
