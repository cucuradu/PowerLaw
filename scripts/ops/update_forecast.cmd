@echo off
rem Pull recent releases and rebuild the forecast (run by the scheduled task; safe to double-click).
cd /d "%~dp0..\.."
".venv\Scripts\python.exe" -m scripts.ops.update_forecast
