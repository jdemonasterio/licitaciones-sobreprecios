"""Copia de seguridad y restauración de la base SIN consola (MEJORAS #49.8, pedido de
jdemonasterio: "backup/restauración de la base sin consola").

Dos operaciones, ambas desde "Datos y fuentes" (`web/app.py`):
  - `hacer_backup()`: copia la base VIVA a un archivo nuevo con la API de backup online de
    SQLite (`sqlite3.Connection.backup`), que no bloquea a otros lectores ni se puede corromper
    a mitad de camino (a diferencia de copiar el archivo .db a mano mientras WAL está activo).
  - `restaurar(contenido)`: valida que el archivo subido sea una base SQLite de ESTA app (header
    + tablas esperadas) y no pase el tope de tamaño, deja la base actual como un backup fechado
    al lado (nunca la borra) y reemplaza el archivo activo de forma atómica (`os.replace`).

Nunca se toca nada fuera de `config.db_path()` y su carpeta: ninguna ruta viene del usuario
(el nombre del backup se genera acá), así que no hay riesgo de traversal ni de borrar algo que
no sea nuestro.
"""
from __future__ import annotations

import datetime as dt
import os
import sqlite3
import tempfile
from pathlib import Path

from . import config, db

MAX_RESTORE_BYTES = 300 * 1024 * 1024   # una base con años de datos ronda decenas de MB
SQLITE_HEADER = b"SQLite format 3\x00"
# Un subconjunto de tablas que solo existen en ESTA app (no cualquier .db SQLite al azar):
# si faltan, no es "la base de sobreprecios", sea cual sea su extensión.
TABLAS_ESPERADAS = ("procesos", "renglones", "comparaciones", "productos", "precios_mercado",
                    "empresas", "revisiones_caso")


def hacer_backup() -> Path:
    """Copia la base viva a un archivo temporal con la API de backup online de SQLite.

    Devuelve la ruta del archivo (en un directorio temporal del sistema): quien llama es
    responsable de servirlo y borrarlo después (ver `web/app.py`, que lo hace con un
    `BackgroundTask` al terminar de mandarlo).
    """
    origen = db.connect()
    try:
        origen.execute("PRAGMA wal_checkpoint(TRUNCATE)")   # vuelca el WAL a el .db antes de copiar
        fd, tmp_name = tempfile.mkstemp(prefix="sobreprecios_backup_", suffix=".db")
        os.close(fd)
        destino = sqlite3.connect(tmp_name)
        try:
            origen.backup(destino)
        finally:
            destino.close()
        return Path(tmp_name)
    finally:
        origen.close()


def nombre_backup(cuando: dt.datetime | None = None) -> str:
    cuando = cuando or dt.datetime.now()
    return f"sobreprecios_backup_{cuando.strftime('%Y%m%d-%H%M%S')}.db"


def validar_archivo_restaurar(contenido: bytes) -> None:
    """Levanta ValueError si `contenido` no es una base SQLite válida de esta app.

    Chequea, en este orden (el más barato primero): tamaño, header binario de SQLite, y que
    tenga las tablas que solo tiene la base de sobreprecios. Nunca ejecuta nada de lo que
    traiga el archivo: solo lee `sqlite_master` y cuenta coincidencias de nombre.
    """
    if not contenido:
        raise ValueError("el archivo está vacío")
    if len(contenido) > MAX_RESTORE_BYTES:
        raise ValueError(f"el archivo pesa más de {MAX_RESTORE_BYTES // (1024 * 1024)} MB: no se puede restaurar")
    if not contenido.startswith(SQLITE_HEADER):
        raise ValueError("el archivo no es una base SQLite (no tiene el encabezado esperado)")

    fd, tmp_name = tempfile.mkstemp(prefix="sobreprecios_restaurar_check_", suffix=".db")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(contenido)
        conn = sqlite3.connect(tmp_name)
        try:
            tablas = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            conn.close()
    finally:
        os.remove(tmp_name)   # archivo de prueba en /tmp (ruta nuestra, nunca la del usuario)
    faltan = [t for t in TABLAS_ESPERADAS if t not in tablas]
    if faltan:
        raise ValueError("el archivo es una base SQLite, pero no es la de esta app "
                         f"(faltan tablas: {', '.join(faltan)})")


def restaurar(contenido: bytes) -> str:
    """Valida, deja la base actual como backup fechado al lado y reemplaza el archivo activo.

    El reemplazo es atómico (`os.replace`, mismo filesystem) y se borran los archivos
    `-wal`/`-shm` de WAL que pudieran haber quedado de la base anterior (si no, SQLite podría
    intentar "seguir" un WAL que ya no corresponde a la base nueva).
    """
    validar_archivo_restaurar(contenido)
    destino = config.db_path()
    destino.parent.mkdir(parents=True, exist_ok=True)

    if destino.exists():
        # Vuelca cualquier WAL pendiente de la base VIEJA antes de moverla: sin esto, un
        # -wal/-shm que quedara al lado de `destino` podria despues intentar "seguir" la base
        # NUEVA que ocupa ese mismo nombre de archivo (el WAL se asocia por ruta, no por contenido).
        try:
            chk = db.connect(destino)
            chk.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            chk.close()
        except sqlite3.Error:
            pass   # si la base actual ya esta rota, se puede restaurar igual por encima

    respaldo = None
    if destino.exists():
        respaldo = destino.parent / f"{destino.stem}.antes-de-restaurar-{dt.datetime.now():%Y%m%d-%H%M%S}{destino.suffix}"
        os.replace(destino, respaldo)   # la base vieja NUNCA se borra, queda fechada al lado
        for sufijo in ("-wal", "-shm"):
            viejo = Path(str(destino) + sufijo)
            if viejo.exists():
                os.replace(viejo, Path(str(respaldo) + sufijo))
    try:
        tmp_fd, tmp_name = tempfile.mkstemp(prefix="sobreprecios_restaurar_", dir=str(destino.parent), suffix=".db")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(contenido)
            os.replace(tmp_name, destino)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)
    except Exception:
        if respaldo is not None and respaldo.exists() and not destino.exists():
            os.replace(respaldo, destino)   # algo falló: no dejar la app sin base
        raise
    for sufijo in ("-wal", "-shm"):
        viejo = Path(str(destino) + sufijo)
        if viejo.exists():
            try:
                viejo.unlink()
            except OSError:
                pass
    msg = "Base restaurada."
    if respaldo is not None:
        msg += f" La base anterior quedó guardada en {respaldo.name}."
    return msg
