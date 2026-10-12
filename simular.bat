@echo off
rem simular.bat -- corre el simulador end-to-end (scripts/simular.py) antes de
rem entregar una version nueva a los usuarios. Abre un Chromium invisible y
rem recorre la app como un usuario real: filtrar, marcar revision, armar un
rem reporte, investigar una licitacion, recalcular precios. No toca
rem data\sobreprecios.db: usa una base y un cache temporales.
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Simulador end-to-end (antes de entregar una version nueva)
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: no se encontro el entorno virtual .venv
    echo Corre primero instalar.bat
    echo Guia: docs\guia_instalacion_windows.md
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts\simular.py %*
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
    echo Todo verde. OK para entregar esta version.
) else (
    echo Algun escenario fallo. Revisa la tabla de arriba y docs\simulador.md.
)
echo.
pause
exit /b %CODIGO%
