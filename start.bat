@echo off
echo Installing dependencies...
pip install -r requirements.txt
echo.
echo Starting LLM Relay...
python main.py
pause
