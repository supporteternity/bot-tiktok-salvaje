@echo off
rem Bot de TikTok para Windows: INICIAR-SESION. Doble clic aqui.
rem Abre el Chrome del bot para iniciar sesion en TikTok a mano.
rem Solo es el boton: el trabajo lo hace scripts\iniciar_sesion.bat, comun a todas las versiones.
rem Si se abre desde dentro del .zip sin descomprimir, Windows solo saca este archivo: se avisa y se para.
if not exist "%~dp0..\scripts\iniciar_sesion.bat" goto sin_descomprimir
if not exist "%~dp0..\pyproject.toml" goto sin_descomprimir
call "%~dp0..\scripts\iniciar_sesion.bat" %*
exit /b %errorlevel%

:sin_descomprimir
echo.
echo Primero hay que descomprimir el archivo .zip del bot:
echo   1. Clic derecho sobre el .zip y "Extraer todo..." y despues "Extraer".
echo   2. En la carpeta que se abre, entra en WINDOWS y haz doble clic en INICIAR-SESION.
echo.
pause
exit /b 1
