@echo off
rem Actualizar (Windows): ejecutar desde la carpeta de la version NUEVA. Para volver atras, desde la
rem carpeta ANTERIOR:  scripts\actualizar.bat --restaurar "<ruta de la copia>"
cd /d "%~dp0.."
call "%~dp0_lugar_seguro_windows.bat" || goto error
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
uv sync --frozen --no-dev || goto error
uv run --no-dev python -m bot actualizar %* || goto error
echo.
pause
exit /b 0
:error
echo Algo ha fallado. Haz una captura de esta ventana y enviala al soporte.
pause
exit /b 1
