@echo off
rem Desinstalar (Windows): doble clic. Para el bot y el panel, quita el arranque automatico y los accesos directos.
rem Los datos NUNCA se borran: se quedan donde estan o, si se elige, se apartan con otro nombre.
cd /d "%~dp0.."
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
echo Esto desinstala el bot de TikTok de esta computadora.
set "EXTRA="
set "RESP="
set /p RESP="Apartar tambien tus datos para que una instalacion nueva empiece vacia? (s/N) "
if /i "%RESP%"=="s" set "EXTRA=--apartar-datos"
if /i "%RESP%"=="si" set "EXTRA=--apartar-datos"
if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m bot desinstalar %EXTRA% || goto error
) else (
  uv run --no-dev python -m bot desinstalar %EXTRA% || goto error
)
echo.
pause
exit /b 0
:error
echo Algo ha fallado. Haz una captura de esta ventana y enviala al soporte.
pause
exit /b 1
