@echo off
cd /d "%~dp0"
echo.
echo  carbon-hub  -  LAN mode
echo  Anyone on the SAME wifi can open the URL printed below.
echo  Do NOT use this on a public network. Ctrl+C to stop.
echo.
".venv\Scripts\streamlit.exe" run app.py --server.address 0.0.0.0 --server.port 8501 --browser.gatherUsageStats false
pause
