"""Tests de scripts/configurar_claves.py: la logica de leer/actualizar .env.

No testeamos input()/print() (son interaccion de consola); testeamos las
funciones puras que leen y escriben el archivo, que son las que pueden
romper datos de la persona si tienen un bug (pisar una clave, perder una
linea de comentario, etc.).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.configurar_claves import actualizar_env, clave_valida, enmascarar, leer_env


def test_actualizar_env_crea_archivo_si_no_existe(tmp_path: Path):
    env_path = tmp_path / ".env"
    actualizar_env(env_path, {"GEMINI_API_KEY": "abc123"})
    assert env_path.exists()
    assert leer_env(env_path) == {"GEMINI_API_KEY": "abc123"}


def test_actualizar_env_preserva_comentarios_y_otras_variables(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text(
        "# nota para el usuario\nOTRA_VAR=no_tocar\nGEMINI_API_KEY=vieja\n",
        encoding="utf-8",
    )
    actualizar_env(env_path, {"GEMINI_API_KEY": "nueva"})
    contenido = env_path.read_text(encoding="utf-8")
    assert "# nota para el usuario" in contenido
    assert "OTRA_VAR=no_tocar" in contenido
    assert "GEMINI_API_KEY=nueva" in contenido
    assert "GEMINI_API_KEY=vieja" not in contenido


def test_actualizar_env_agrega_variable_nueva_al_final_sin_tocar_el_resto(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    actualizar_env(env_path, {"OPENROUTER_API_KEY": "sk-or-xyz"})
    valores = leer_env(env_path)
    assert valores["GEMINI_API_KEY"] == "abc"
    assert valores["OPENROUTER_API_KEY"] == "sk-or-xyz"


def test_actualizar_env_sin_cambios_no_rompe_nada(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("GEMINI_API_KEY=abc\n", encoding="utf-8")
    actualizar_env(env_path, {})
    assert leer_env(env_path) == {"GEMINI_API_KEY": "abc"}


def test_leer_env_ignora_comentarios_y_lineas_vacias(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text("# nota\n\nGEMINI_API_KEY=xyz\n", encoding="utf-8")
    assert leer_env(env_path) == {"GEMINI_API_KEY": "xyz"}


def test_leer_env_archivo_inexistente_devuelve_vacio(tmp_path: Path):
    assert leer_env(tmp_path / "no_existe.env") == {}


def test_leer_env_quita_comillas(tmp_path: Path):
    env_path = tmp_path / ".env"
    env_path.write_text('APPSMITH_SESSION=\'valor con $ y ;\'\n', encoding="utf-8")
    assert leer_env(env_path) == {"APPSMITH_SESSION": "valor con $ y ;"}


@pytest.mark.parametrize(
    "valor,esperado",
    [
        ("sk-abc123", True),
        ("sk-or-v1-abcdef", True),
        ("", False),
        ("   ", False),
        ("con espacio", False),
        ("tiene\ttab", False),
        ("con\nsalto", False),
    ],
)
def test_clave_valida_rechaza_vacias_y_con_espacios(valor, esperado):
    assert clave_valida(valor) is esperado


def test_enmascarar_muestra_solo_los_ultimos_4_caracteres():
    clave = "sk-or-v1-abcdWXYZ"
    esperado = "*" * (len(clave) - 4) + "WXYZ"
    assert enmascarar(clave) == esperado


def test_enmascarar_valor_vacio():
    assert enmascarar("") == "(vacio)"
    assert enmascarar(None) == "(vacio)"  # type: ignore[arg-type]


def test_enmascarar_valor_corto_no_revela_nada():
    assert enmascarar("abcd") == "****"
