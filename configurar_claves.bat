@echo off
rem configurar_claves.bat -- asistente para cargar las claves de LLM (.env).
rem Corré esto despues de instalar.bat, y de nuevo cuando quieras cambiar
rem o agregar una clave.
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ERROR: no se encontro el entorno virtual .venv
    echo Corre primero instalar.bat
    echo Guia: docs\guia_instalacion_windows.md
    echo.
    pause
    exit /b 1
)

".venv\Scripts\python.exe" scripts\configurar_claves.py
if errorlevel 1 (
    echo.
    echo Hubo un problema. Revisa docs\preguntas_frecuentes.md
)

echo.
pause
