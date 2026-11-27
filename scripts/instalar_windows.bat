@echo off
rem Instalador para Windows (se abre desde WINDOWS\INSTALAR.bat).
rem Instala uv si falta, las dependencias exactas, Google Chrome si falta y el arranque automatico.
cd /d "%~dp0.."
if not exist "pyproject.toml" goto sin_descomprimir
call "%~dp0_lugar_seguro_windows.bat" || goto error
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>nul
if not errorlevel 1 goto hay_uv
echo Instalando uv (el programa que prepara Python para el bot)...
powershell -NoProfile -ExecutionPolicy ByPass -Command "irm https://astral.sh/uv/install.ps1 | iex"
where uv >nul 2>nul
if errorlevel 1 goto sin_uv
:hay_uv
echo Instalando lo que necesita el bot (la primera vez tarda unos minutos)...
uv sync --frozen --no-dev || goto error
uv run --frozen --no-dev python -m bot instalar || goto error
echo.
pause
exit /b 0

:sin_uv
echo.
echo No se pudo instalar uv. Comprueba la conexion a internet y vuelve a abrir el instalador.
goto error

:sin_descomprimir
echo.
echo Primero hay que descomprimir el archivo .zip del bot (clic derecho, "Extraer todo...").
goto error

:error
echo.
echo Algo ha fallado. Haz una captura de esta ventana y enviala al soporte.
pause
exit /b 1
