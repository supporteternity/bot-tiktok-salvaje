@echo off
rem Se llama (call) desde instalar_windows.bat y actualizar.bat, con la carpeta del bot como carpeta actual.
rem Si la carpeta esta en Descargas, Escritorio, Documentos, OneDrive o una carpeta temporal, se copia a
rem %LOCALAPPDATA%\Programs\Bot TikTok\ y se sigue desde la copia (cd): esas carpetas se limpian o se
rem sincronizan y el bot dejaria de arrancar. La carpeta original no se toca (sirve para volver atras).
rem Tambien quita la marca "descargado de internet" para que los demas scripts se abran sin aviso.
rem Sin setlocal: el cambio de carpeta (cd) tiene que llegar al script que llama.
set "BOT_FECHA="
for /f %%F in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd-HHmmss"') do set "BOT_FECHA=%%F"
if "%BOT_FECHA%"=="" set "BOT_FECHA=copia"
set "BOT_DESTINO=%LOCALAPPDATA%\Programs\Bot TikTok\BOT-TIKTOK-APP-%BOT_FECHA%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0_lugar_seguro_windows.ps1" -App "%CD%" -Destino "%BOT_DESTINO%"
if errorlevel 2 exit /b 1
if errorlevel 1 cd /d "%BOT_DESTINO%"
exit /b 0
