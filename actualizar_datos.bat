@echo off
rem actualizar_datos.bat -- corre el pipeline completo para los ultimos 60
rem dias (Boletin Oficial -> extraer -> machear -> INDEC -> comparar).
rem Puede tardar bastante: descarga datos y usa el LLM. Es normal.
chcp 65001 >nul
cd /d "%~dp0"

echo ============================================================
echo  Actualizando datos (ultimos 60 dias)...
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

set "DESDE="
set "HASTA="
for /f "delims=" %%d in ('".venv\Scripts\python.exe" -c "import datetime as dt; print(dt.date.today().isoformat())"') do set "HASTA=%%d"
for /f "delims=" %%d in ('".venv\Scripts\python.exe" -c "import datetime as dt; print((dt.date.today()-dt.timedelta(days=60)).isoformat())"') do set "DESDE=%%d"

if not defined DESDE goto :fechas_mal
if not defined HASTA goto :fechas_mal

echo Rango: %DESDE% a %HASTA%
echo Esto puede tardar bastante (descarga del Boletin Oficial + LLM).
echo Si el plan gratis del LLM se queda sin cuota, el pipeline sigue
echo solo con reglas -- no se traba, solo deja menos renglones clasificados.
echo.

rem Primero la foto de precios de HOY: no existe archivo historico publico, cada dia
rem que no se corre es un dia de precios que se pierde para siempre.
echo [1/2] Precios de hoy (supermercados y mayoristas)...
".venv\Scripts\python.exe" -m sobreprecios.cli precios-hoy
echo.
echo [2/2] Boletin Oficial y COMPR.AR de los ultimos 60 dias...
".venv\Scripts\python.exe" -m sobreprecios.cli pipeline --desde %DESDE% --hasta %HASTA%
if errorlevel 1 (
    echo.
    echo ERROR durante la actualizacion. Revisa el mensaje de arriba y
    echo docs\preguntas_frecuentes.md
    echo.
    pause
    exit /b 1
)

echo.
echo Listo. Los datos nuevos ya estan en la base.
echo Abri iniciar.bat para verlos en el navegador.
pause
exit /b 0

:fechas_mal
echo.
echo ERROR: no se pudieron calcular las fechas (revisa que Python ande bien).
echo Revisa docs\preguntas_frecuentes.md
echo.
pause
exit /b 1
