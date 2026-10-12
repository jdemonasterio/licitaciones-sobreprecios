@echo off
rem iniciar.bat -- levanta el webui y abre el navegador.
rem No cierres la ventana negra mientras uses la aplicacion: es el motor.
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Iniciando licitaciones-sobreprecios...
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

if not exist ".env" (
    echo ERROR: no se encontro el archivo .env
    echo Corre instalar.bat y despues configurar_claves.bat
    echo.
    pause
    exit /b 1
)

echo El navegador se va a abrir solo en unos segundos en:
echo   http://127.0.0.1:8000
echo.
echo NO CIERRES esta ventana negra mientras usas la aplicacion.
echo Para apagarla: cerra esta ventana o apreta Ctrl+C.
echo.

rem Pequeña espera (via ping, mas confiable que "timeout" en este contexto)
rem para dar tiempo a que el servidor levante antes de abrir el navegador.
start "" /b cmd /c "ping -n 4 127.0.0.1 >nul & start http://127.0.0.1:8000"

".venv\Scripts\python.exe" -m sobreprecios.cli servir

echo.
echo La aplicacion se detuvo.
pause
