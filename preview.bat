@echo off
rem Exports without uploading and opens the site locally at http://localhost:8765
python "%~dp0tools\export.py" --no-push
start "" http://localhost:8765/
python -m http.server 8765 -d "%~dp0docs"
