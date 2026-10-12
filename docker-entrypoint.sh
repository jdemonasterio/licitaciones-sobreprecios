#!/bin/sh
# Arranque del contenedor: primer boot con la base vacia -> carga catalogo + INDEC +
# cotizaciones (setup, sin el dataset demo 2021 porque no viaja en la imagen) antes de
# levantar el webui. Si la base YA existe (volumen persistente de una corrida anterior),
# se la deja como esta y se va directo a "servir": idempotente a nivel de archivo, nunca
# reprocesa lo que ya esta.
#
# Si el setup del primer boot falla (ej. sin internet en ese momento), no tumba el
# contenedor: el webui igual levanta, vacio, y se puede reintentar a mano mas tarde
# (ver docs/despliegue_nube.md "Problemas").
set -eu

DB_PATH="${LIC_DB_PATH:-/data/sobreprecios.db}"
mkdir -p "$(dirname "$DB_PATH")"

if [ ! -f "$DB_PATH" ]; then
    echo "[entrypoint] primer boot: no existe $DB_PATH -> corriendo 'setup' (catalogo + INDEC + cotizaciones)"
    python -m sobreprecios.cli setup --sin-demo || echo "[entrypoint] 'setup' termino con errores; sigo e igual levanto el webui (reintentar desde 'Datos y fuentes' o redeploy)"
else
    echo "[entrypoint] ya existe $DB_PATH -> salto 'setup'"
fi

exec python -m sobreprecios.cli servir --host 0.0.0.0 --puerto "${PORT:-8000}"
