@echo off
rem dist\LightCore klasorunu uretir (exe, gomulu Python). Yayim paketi icin yanina third_party\openrgb
rem (OpenRGB Windows) ve third_party\fan (LibreHardwareMonitorLib ve bagimliliklari, FanHost.exe) kopyalanir.
cd /d "%~dp0"
python -m pip install --upgrade pyinstaller bleak || goto hata
python -m PyInstaller --noconfirm --clean --onedir --noconsole --uac-admin --optimize 2 --name LightCore --icon assets\lightcore.ico --add-data "lightcore\FanHost.cs;lightcore" --hidden-import lightcore.ui_dash --hidden-import lightcore.ui_fans --hidden-import lightcore.ui_shots --hidden-import lightcore.ui_settings --hidden-import lightcore.game --hidden-import lightcore.ble --collect-submodules bleak --collect-all winrt app.py || goto hata
if exist third_party\openrgb xcopy /e /i /y third_party\openrgb dist\LightCore\openrgb
if exist third_party\fan xcopy /e /i /y third_party\fan dist\LightCore\fan
echo Hazir: dist\LightCore\LightCore.exe
pause
exit /b 0
:hata
echo Derleme basarisiz.
pause
exit /b 1
