"""Asistente interactivo para cargar las claves de LLM en el .env.

Que hace: pregunta, una por una, las claves de Gemini / OpenRouter / OpenAI
y el perfil de uso (gratis/pago), y las guarda en el `.env` de la raiz del
proyecto SIN pisar ninguna otra linea que ya este ahi (comentarios, otras
variables). Si el usuario aprieta Enter sin escribir nada, esa variable
queda como estaba (no se borra ni se vacia).

Nunca imprime una clave completa por pantalla: solo los ultimos 4
caracteres (ver `enmascarar`), para que la persona pueda reconocer "la de
siempre" sin que quede expuesta en la consola o en una captura de pantalla.

Como se usa:
    - Doble click en configurar_claves.bat (lo invoca con el Python del
      entorno virtual), o
    - directamente: `.venv\\Scripts\\python scripts\\configurar_claves.py`

Diseño: las funciones que tocan archivos (`leer_env`, `actualizar_env`) y
las de validacion (`clave_valida`, `enmascarar`) estan separadas de
`input()`/`print()` a proposito, para poder testearlas sin simular la
consola real -- ver tests/test_configurar_claves.py.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = REPO_DIR / ".env"

# Nombre -> (obligatoria-ish, descripcion para el prompt)
_PREGUNTAS_CLAVE = [
    ("GEMINI_API_KEY", True, "Gemini -- gratis, la recomendada por defecto"),
    ("OPENROUTER_API_KEY", False, "OpenRouter -- respaldo gratis, opcional"),
    ("OPENAI_API_KEY", False, "OpenAI -- de pago, opcional"),
]


def leer_env(env_path: Path) -> dict[str, str]:
    """Lee un .env simple (KEY=valor por linea) y devuelve lo que encuentra.

    Ignora comentarios (#) y lineas vacias o sin "=". No falla si el
    archivo no existe: devuelve un diccionario vacio.
    """
    valores: dict[str, str] = {}
    if not env_path.exists():
        return valores
    for linea in env_path.read_text(encoding="utf-8").splitlines():
        limpia = linea.strip()
        if not limpia or limpia.startswith("#") or "=" not in limpia:
            continue
        clave, _, valor = limpia.partition("=")
        valores[clave.strip()] = valor.strip().strip('"').strip("'")
    return valores


def enmascarar(valor: str) -> str:
    """Devuelve una version segura para mostrar: solo los ultimos 4 chars."""
    valor = (valor or "").strip()
    if not valor:
        return "(vacio)"
    if len(valor) <= 4:
        return "*" * len(valor)
    return "*" * (len(valor) - 4) + valor[-4:]


def clave_valida(valor: str) -> bool:
    """Forma minima de una API key: no vacia y sin espacios/tabs/saltos."""
    valor = (valor or "").strip()
    return bool(valor) and re.search(r"\s", valor) is None


def actualizar_env(env_path: Path, nuevos_valores: dict[str, str]) -> None:
    """Escribe/actualiza SOLO las claves de `nuevos_valores` en env_path.

    Preserva cualquier otra linea tal cual esta (comentarios, otras
    variables, orden). Si una clave ya existe, reemplaza esa linea; si no
    existe, la agrega al final. Crea el archivo si no existia.
    """
    lineas_originales = (
        env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    )
    pendientes = dict(nuevos_valores)
    lineas_nuevas: list[str] = []
    for linea in lineas_originales:
        limpia = linea.strip()
        if "=" in limpia and not limpia.startswith("#"):
            clave = limpia.split("=", 1)[0].strip()
            if clave in pendientes:
                lineas_nuevas.append(f"{clave}={pendientes.pop(clave)}")
                continue
        lineas_nuevas.append(linea)
    for clave, valor in pendientes.items():
        lineas_nuevas.append(f"{clave}={valor}")
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(lineas_nuevas) + "\n", encoding="utf-8")


def _preguntar_clave(nombre: str, obligatoria: bool, descripcion: str, actual: str | None) -> str | None:
    etiqueta = "recomendada" if obligatoria else "opcional"
    estado = f"ya guardada, termina en {enmascarar(actual)}" if actual else "no configurada todavia"
    print(f"\n{nombre} ({etiqueta}) -- {descripcion}")
    print(f"  Estado actual: {estado}")
    try:
        respuesta = input("  Pegá el valor nuevo (Enter para dejar como esta): ").strip()
    except EOFError:
        respuesta = ""
    if not respuesta:
        return None
    if not clave_valida(respuesta):
        print("  Ojo: eso no parece una clave valida (vacia o con espacios). No se guardo.")
        return None
    return respuesta


def _preguntar_perfil(actual: str | None) -> str | None:
    perfil_actual = actual or "gratis"
    print(f"\nLIC_LLM_PERFIL (perfil de uso: gratis o pago) -- actual: {perfil_actual}")
    try:
        respuesta = input("  Escribi 'gratis' o 'pago' (Enter para dejar como esta): ").strip().lower()
    except EOFError:
        respuesta = ""
    if not respuesta:
        return None
    if respuesta in {"gratis", "pago"}:
        return respuesta
    print("  No entendi esa opcion ('gratis' o 'pago'). No se cambio nada.")
    return None


def main() -> int:
    print("=" * 60)
    print(" Configuracion de claves de LLM -- licitaciones-sobreprecios")
    print("=" * 60)
    print("Las claves se guardan en .env, solo en esta computadora.")
    print("Nunca se suben a git ni se muestran completas por pantalla.")
    print("Si no tenes una clave todavia, apreta Enter y la conseguis")
    print("despues (ver docs/guia_claves_api.md).")

    actuales = leer_env(ENV_PATH)
    nuevos: dict[str, str] = {}

    for nombre, obligatoria, descripcion in _PREGUNTAS_CLAVE:
        valor = _preguntar_clave(nombre, obligatoria, descripcion, actuales.get(nombre))
        if valor:
            nuevos[nombre] = valor

    perfil = _preguntar_perfil(actuales.get("LIC_LLM_PERFIL"))
    if perfil:
        nuevos["LIC_LLM_PERFIL"] = perfil

    if nuevos:
        actualizar_env(ENV_PATH, nuevos)
        print(f"\nListo. Se actualizo .env con: {', '.join(nuevos.keys())}")
    else:
        print("\nNo se cambio nada.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
