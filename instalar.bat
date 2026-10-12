@echo off
rem instalar.bat -- primera instalacion de licitaciones-sobreprecios.
rem Busca Python 3.12, crea el entorno virtual, instala dependencias,
rem prepara el .env y crea la base de datos con el catalogo.
rem Se corre UNA sola vez (o de nuevo si algo se rompio). Despues, usar
rem configurar_claves.bat y luego iniciar.bat.
chcp 65001 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Instalacion de licitaciones-sobreprecios
echo ============================================================
echo.

echo [1/5] Buscando Python 3.12...
set "PYTHON_CMD="
py -3.12 --version >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_CMD=py -3.12"
)
if not defined PYTHON_CMD (
    python --version >nul 2>&1
    if not errorlevel 1 (
        set "PYTHON_CMD=python"
    )
)

if not defined PYTHON_CMD (
    echo.
    echo ERROR: no se encontro Python instalado.
    echo Instala Python 3.12 desde https://www.python.org/downloads/
    echo IMPORTANTE: en el instalador, tilda "Add python.exe to PATH"
    echo antes de hacer click en "Install Now".
    echo Guia completa: docs\guia_instalacion_windows.md
    echo.
    pause
    exit /b 1
)
echo     encontrado: !PYTHON_CMD!
echo.

echo [2/5] Creando entorno virtual (.venv)...
if exist ".venv\Scripts\python.exe" (
    echo     .venv ya existe, se reutiliza.
) else (
    !PYTHON_CMD! -m venv .venv
    if errorlevel 1 (
        echo.
        echo ERROR: no se pudo crear el entorno virtual .venv
        echo Revisa docs\preguntas_frecuentes.md
        echo.
        pause
        exit /b 1
    )
)
echo.

echo [3/5] Instalando dependencias (puede tardar unos minutos)...
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>&1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: fallo la instalacion de dependencias.
    echo Revisa tu conexion a internet y docs\preguntas_frecuentes.md
    echo.
    pause
    exit /b 1
)
echo.

echo [4/5] Preparando archivo .env...
if exist ".env" (
    echo     .env ya existe, no se toca.
) else (
    copy ".env.example" ".env" >nul
    echo     se creo .env a partir de .env.example.
    echo     Despues de este script, corre configurar_claves.bat para
    echo     cargar tu clave de Gemini.
)
echo.

rem Permisos recomendados para Claude Code (aceptar ediciones, comandos del proyecto permitidos,
rem leer .env / borrar data / git push bloqueados). Se copia solo si todavia no existe.
if not exist ".claude\settings.json" (
    if not exist ".claude" mkdir ".claude"
    copy "docs\claude_settings_recomendado.json" ".claude\settings.json" >nul
    echo     se configuro Claude Code con permisos seguros (.claude\settings.json).
)
echo.

echo [5/5] Creando la base de datos y cargando el catalogo...
".venv\Scripts\python.exe" -m sobreprecios.cli setup
if errorlevel 1 (
    echo.
    echo ERROR durante "cli setup". Revisa el mensaje de arriba y
    echo docs\preguntas_frecuentes.md
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================
echo  Listo! Proximos pasos:
echo    1. configurar_claves.bat   (cargar tu clave de Gemini)
echo    2. iniciar.bat             (abrir la aplicacion)
echo ============================================================
echo.
pause
