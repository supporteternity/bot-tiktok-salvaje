@echo off
rem Abre Chrome con el perfil del bot para iniciar sesion en TikTok a mano (para el bot mientras tanto).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m bot iniciar-sesion
echo.
pause
