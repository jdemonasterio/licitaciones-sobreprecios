"""Tests de sobreprecios/backup.py (MEJORAS #49.8, "backup/restauración sin consola"):
backup online de SQLite, validación del archivo a restaurar y el reemplazo atómico con backup
fechado. Todo sobre bases en tmp_path (nunca la base real: ver ~/.claude/rules/security.md)."""
from __future__ import annotations

import sqlite3

import pytest

from sobreprecios import backup, db


def test_hacer_backup_copia_datos_vivos(tmp_path, monkeypatch):
    db_path = tmp_path / "t.db"
    monkeypatch.setenv("LIC_DB_PATH", str(db_path))
    with db.session(db_path) as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, organismo, created_at, updated_at) "
                    "VALUES ('bo_primera', 'x1', 'ORG X', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()

    tmp_backup = backup.hacer_backup()
    try:
        assert tmp_backup.exists()
        conn2 = sqlite3.connect(str(tmp_backup))
        row = conn2.execute("SELECT organismo FROM procesos WHERE external_id='x1'").fetchone()
        conn2.close()
        assert row == ("ORG X",)
    finally:
        tmp_backup.unlink(missing_ok=True)


def test_nombre_backup_tiene_fecha_y_extension():
    import datetime as dt
    nombre = backup.nombre_backup(dt.datetime(2026, 10, 12, 15, 30, 0))
    assert nombre == "sobreprecios_backup_20261012-153000.db"


def test_validar_archivo_rechaza_vacio():
    with pytest.raises(ValueError, match="vacío"):
        backup.validar_archivo_restaurar(b"")


def test_validar_archivo_rechaza_no_sqlite():
    with pytest.raises(ValueError, match="no es una base SQLite"):
        backup.validar_archivo_restaurar(b"esto no es una base de datos para nada")


def test_validar_archivo_rechaza_demasiado_grande(monkeypatch):
    monkeypatch.setattr(backup, "MAX_RESTORE_BYTES", 10)
    with pytest.raises(ValueError, match="pesa más"):
        backup.validar_archivo_restaurar(backup.SQLITE_HEADER + b"x" * 20)


def test_validar_archivo_rechaza_sqlite_sin_tablas_esperadas(tmp_path):
    otra = tmp_path / "otra.db"
    conn = sqlite3.connect(str(otra))
    conn.execute("CREATE TABLE cosas (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="no es la de esta app"):
        backup.validar_archivo_restaurar(otra.read_bytes())


def test_validar_archivo_acepta_base_valida(tmp_path):
    db_path = tmp_path / "valida.db"
    with db.session(db_path):
        pass
    backup.validar_archivo_restaurar(db_path.read_bytes())   # no debe levantar


def test_restaurar_reemplaza_y_deja_backup_fechado(tmp_path, monkeypatch):
    destino = tmp_path / "activa.db"
    monkeypatch.setenv("LIC_DB_PATH", str(destino))
    with db.session(destino) as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, organismo, created_at, updated_at) "
                    "VALUES ('bo_primera', 'viejo', 'ORG VIEJO', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()

    nueva_path = tmp_path / "nueva.db"
    with db.session(nueva_path) as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, organismo, created_at, updated_at) "
                    "VALUES ('bo_primera', 'nuevo', 'ORG NUEVO', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()

    msg = backup.restaurar(nueva_path.read_bytes())
    assert "restaurada" in msg.lower()

    with db.session(destino) as conn:
        rows = [dict(r) for r in conn.execute("SELECT external_id FROM procesos")]
    assert {r["external_id"] for r in rows} == {"nuevo"}

    backups = list(tmp_path.glob("activa.antes-de-restaurar-*.db"))
    assert len(backups) == 1
    conn = sqlite3.connect(str(backups[0]))
    vieja = conn.execute("SELECT organismo FROM procesos WHERE external_id='viejo'").fetchone()
    conn.close()
    assert vieja == ("ORG VIEJO",)


def test_restaurar_sin_base_previa_no_rompe(tmp_path, monkeypatch):
    destino = tmp_path / "nueva_instalacion.db"
    monkeypatch.setenv("LIC_DB_PATH", str(destino))
    origen_path = tmp_path / "origen.db"
    with db.session(origen_path) as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, created_at, updated_at) "
                    "VALUES ('bo_primera', 'o1', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()

    msg = backup.restaurar(origen_path.read_bytes())
    assert "restaurada" in msg.lower()
    assert destino.exists()
    assert not list(tmp_path.glob("*.antes-de-restaurar-*.db"))   # nada que respaldar la primera vez


def test_restaurar_rechaza_archivo_invalido_no_toca_la_base(tmp_path, monkeypatch):
    destino = tmp_path / "activa.db"
    monkeypatch.setenv("LIC_DB_PATH", str(destino))
    with db.session(destino) as conn:
        conn.execute("INSERT INTO procesos (fuente, external_id, organismo, created_at, updated_at) "
                    "VALUES ('bo_primera', 'original', 'ORG ORIGINAL', ?, ?)", (db.now_iso(), db.now_iso()))
        conn.commit()

    with pytest.raises(ValueError):
        backup.restaurar(b"no es una base de datos")

    with db.session(destino) as conn:
        rows = [dict(r) for r in conn.execute("SELECT external_id FROM procesos")]
    assert {r["external_id"] for r in rows} == {"original"}
    assert not list(tmp_path.glob("*.antes-de-restaurar-*.db"))
