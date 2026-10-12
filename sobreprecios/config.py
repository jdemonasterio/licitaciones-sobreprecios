"""Rutas del repo y lectura de config/*.yml y .env.

La config se relee en cada llamada (no se cachea al importar): editar un YAML
tiene efecto en la próxima corrida o el próximo request del webui, sin reiniciar.

`load_env()` vive dentro de una función y no a nivel de módulo, para que los
tests puedan apuntar a un .env falso y el .env de desarrollo no les pise las
variables (nos pasó en telegram-transcribe-bot).
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

REPO_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = REPO_DIR / "config"
PROMPTS_DIR = REPO_DIR / "prompts"
SCRATCH_DIR = REPO_DIR / "_scratch"


def load_env(dotenv_path: Path | None = None) -> None:
    """Carga .env sin pisar variables ya definidas en el entorno."""
    from dotenv import load_dotenv
    load_dotenv(dotenv_path or REPO_DIR / ".env", override=False)


def load_yaml(name: str) -> dict:
    with open(CONFIG_DIR / name, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def settings() -> dict:
    return load_yaml("settings.yml")


def resolve(path_like: str | Path) -> Path:
    """Rutas relativas de settings.yml son relativas a la raíz del repo."""
    path = Path(path_like)
    return path if path.is_absolute() else REPO_DIR / path


def db_path() -> Path:
    override = os.getenv("LIC_DB_PATH")
    return resolve(override or settings()["db_path"])


def cache_dir(*parts: str) -> Path:
    """Directorio de cache (creado si no existe): data/cache/<parts...>."""
    base = resolve(os.getenv("LIC_CACHE_DIR") or settings()["cache_dir"])
    path = base.joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def scratch_path(project: str, filename: str) -> Path:
    """Ruta en _scratch/<project>/ para goldens y análisis (nunca commiteados).

    Bloquea path traversal: solo el basename de `filename` se usa.
    """
    safe_project = os.path.basename(project.strip()) or "misc"
    safe_name = os.path.basename(filename.strip())
    if not safe_name or safe_name in {".", ".."}:
        raise ValueError(f"nombre de archivo inválido: {filename!r}")
    folder = SCRATCH_DIR / safe_project
    folder.mkdir(parents=True, exist_ok=True)
    return folder / safe_name
