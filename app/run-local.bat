@echo off
cd /d "%~dp0"
echo.
echo  carbon-hub  -  local mode
echo  URL: http://127.0.0.1:8501
echo  (only this computer can open it)
echo.
".venv\Scripts\streamlit.exe" run app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
pause
