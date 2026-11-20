@echo off
cd /d "%~dp0"
fltmc >nul 2>&1 || (echo UYARI: Yonetici olarak calistirmadin. RAM, anakart ve fanlar calismaz. Sag tik, Yonetici olarak calistir. & pause)
where pythonw >nul 2>nul && (start "" pythonw -OO -S app.py & exit /b)
python -OO -S app.py
pause
