#!/usr/bin/env python3
"""Simulador end-to-end: corre tests/e2e/ y da un PASS/FAIL por escenario.

Por que existe: los ~175 tests unitarios (`.venv/bin/python -m pytest -q`)
prueban funciones y rutas por separado, pero nada abre un navegador real y
sigue un journey completo (filtrar, marcar revision, armar un reporte,
investigar una licitacion, recalcular precios). Este script es el "gate"
antes de entregarle una version nueva a los usuarios: igual que el
post_deploy.py de telegram-transcribe-bot, corre la bateria y devuelve un
codigo de salida distinto de cero si algo rompio.

Uso:
    .venv/bin/python scripts/simular.py              # toda la bateria
    .venv/bin/python scripts/simular.py -k revision   # solo los que matcheen
    .venv/bin/python scripts/simular.py -v            # salida completa de pytest

En Windows: simular.bat (en la raiz del repo) hace lo mismo con el Python
del entorno virtual (.venv\\Scripts\\python.exe).

Que hace:
  1. Prende LIC_E2E=1 (sin esto tests/e2e/ se skippea entero, ver conftest.py)
     y LIC_E2E_SHOTS=_scratch/licitaciones/simulador/<timestamp>.
  2. Corre `pytest tests/e2e -v` (stdlib + lo que ya esta en el repo: no
     agrega dependencias nuevas).
  3. Parsea la salida verbose de pytest y arma una tabla PASS/FAIL por
     escenario (nombre corto, en castellano, sin el ruido de pytest).
  4. Si algo fallo, el conftest.py ya guardo un screenshot de cada falla en
     esa carpeta de _scratch/; este script solo avisa donde quedaron.
  5. Sale con el mismo codigo que pytest (0 = todo bien, != 0 = hay que mirar).

No crea una base ni un cache nuevos a mano: eso lo hace tests/e2e/conftest.py
en una carpeta temporal (nunca data/sobreprecios.db) con la semilla offline
del propio codigo del repo.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
VENV_PY = REPO / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
RESULT_RE = re.compile(r"^(?P<nodeid>\S+::\S+)\s+(?P<outcome>PASSED|FAILED|ERROR|SKIPPED)\b")


def _short_name(nodeid: str) -> str:
    """'tests/e2e/test_user_journeys.py::test_inicio_lista_...' -> 'inicio lista ...'."""
    name = nodeid.split("::", 1)[-1]
    name = re.sub(r"^test_", "", name)
    return name.replace("_", " ")


def _python() -> str:
    if VENV_PY.exists():
        return str(VENV_PY)
    print(f"AVISO: no encontre {VENV_PY}, uso {sys.executable}", file=sys.stderr)
    return sys.executable


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("-k", dest="k", default=None, help="filtro de pytest -k (nombre de escenario)")
    parser.add_argument("-v", "--verbose", action="store_true", help="mostrar la salida completa de pytest")
    args = parser.parse_args(argv)

    timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    shots_dir = REPO / "_scratch" / "licitaciones" / "simulador" / timestamp

    env = dict(os.environ)
    env["LIC_E2E"] = "1"
    env["LIC_E2E_SHOTS"] = str(shots_dir)

    cmd = [_python(), "-m", "pytest", "tests/e2e", "-v", "--tb=short", "-p", "no:cacheprovider"]
    if args.k:
        cmd += ["-k", args.k]

    print("=" * 72)
    print(" Simulador end-to-end — licitaciones-sobreprecios")
    print("=" * 72)
    print(f"Corriendo: {' '.join(cmd)}")
    print()

    proc = subprocess.run(cmd, cwd=str(REPO), env=env, capture_output=True, text=True)
    salida = proc.stdout + proc.stderr
    if args.verbose:
        print(salida)

    resultados: list[tuple[str, str]] = []
    for line in salida.splitlines():
        m = RESULT_RE.match(line.strip())
        if m:
            resultados.append((m.group("nodeid"), m.group("outcome")))

    if not resultados:
        print("No se pudo parsear ningun resultado de pytest. Salida completa:\n")
        print(salida)
        return proc.returncode or 1

    ancho = max(len(_short_name(n)) for n, _ in resultados) + 2
    print(f"{'Escenario':<{ancho}} Resultado")
    print("-" * (ancho + 12))
    for nodeid, outcome in resultados:
        icono = {"PASSED": "OK  ", "FAILED": "FAIL", "ERROR": "ERR ", "SKIPPED": "SKIP"}[outcome]
        print(f"{_short_name(nodeid):<{ancho}} {icono}")

    passed = sum(1 for _, o in resultados if o == "PASSED")
    failed = [n for n, o in resultados if o in ("FAILED", "ERROR")]
    skipped = sum(1 for _, o in resultados if o == "SKIPPED")
    print("-" * (ancho + 12))
    print(f"{passed}/{len(resultados)} OK" + (f", {skipped} skipped" if skipped else ""))

    if failed:
        print(f"\n{len(failed)} escenario(s) fallaron:")
        for n in failed:
            print(f"  - {_short_name(n)}")
        if shots_dir.exists():
            print(f"\nScreenshots de las fallas: {shots_dir}")
        print("\nSalida completa de pytest (para ver el detalle de cada falla):")
        print(salida)
    else:
        print("\nTodo verde. OK para entregar esta version.")

    return proc.returncode


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
