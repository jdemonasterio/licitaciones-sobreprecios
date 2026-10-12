"""Diccionario de abreviaturas / token-families por producto (MEJORAS #1, P22).

Por qué: muchas licitaciones y descripciones de mercado abrevian el nombre del
producto ("FID" por "fideos", "LCH" por "leche") o un atributo ("P DESCR" por
"parcialmente descremada"). `sobreprecios.catalog.match_rules` matchea por
frases COMPLETAS (`incluye`/`excluye` de config/productos.yml): una
abreviatura no reconocida deja el renglón sin producto aunque un humano lo
identifique a simple vista.

`expandir()` agrega, ANTES de tokenizar para el matcher, los tokens de la
expansión de cada abreviatura APROBADA presente como tokens completos y
contiguos (nunca substring: "fid" no debe matchear "confidencial"). Es
puramente ADITIVO — nunca borra ni reemplaza texto — así una exclusión que ya
buscaba el token abreviado literal sigue funcionando igual; e idempotente:
aplicarlo dos veces da el mismo conjunto de tokens relevantes.

`minar_candidatos()` es la otra mitad (CLI `abreviaturas --buscar`): mina
descripciones SIN producto buscando tokens cortos frecuentes que podrían ser
abreviaturas de una palabra de algún producto del catálogo (por prefijo o por
"esqueleto" sin vocales), y las deja en config/abreviaturas.yml con
estado=propuesta — NUNCA aprobadas automáticamente, con o sin LLM.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import config, llm
from .textnorm import norm

logger = logging.getLogger(__name__)

FILENAME = "abreviaturas.yml"

# Conectores y unidades de medida: nunca candidatos a abreviatura (demasiado ruido / ambiguos).
_STOPWORDS = {
    "de", "del", "la", "las", "los", "el", "en", "y", "e", "o", "u", "a", "con", "sin", "por",
    "para", "un", "una", "unos", "unas", "al", "su", "sus", "no", "si",
    "x", "c", "s", "n", "nro", "num",
    "gr", "grs", "g", "kg", "kgs", "kilo", "kilos", "ml", "cc", "lt", "lts", "l", "u", "un",
    "und", "unid", "unids", "cm", "mm", "mt", "mts",
}


@dataclass(frozen=True)
class Abreviatura:
    abreviatura: str                 # texto tal como se guarda (puede tener espacios: "p descr")
    expande: str
    producto_id: str | None = None
    estado: str = "propuesta"        # aprobada | propuesta | rechazada
    evidencia: tuple[str, ...] = ()
    fecha: str | None = None
    fuente: str | None = None
    nota: str | None = None
    llm_veredicto: dict | None = None

    @property
    def tokens_abrev(self) -> tuple[str, ...]:
        return tuple(norm(self.abreviatura).split())

    @property
    def tokens_expande(self) -> tuple[str, ...]:
        return tuple(norm(self.expande).split())


def _yaml_path() -> Path:
    return config.CONFIG_DIR / FILENAME


def _raw() -> dict:
    path = _yaml_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _from_item(item: dict) -> Abreviatura:
    return Abreviatura(
        abreviatura=str(item["abreviatura"]),
        expande=str(item["expande"]),
        producto_id=item.get("producto_id"),
        estado=item.get("estado", "propuesta"),
        evidencia=tuple(item.get("evidencia") or []),
        fecha=item.get("fecha"),
        fuente=item.get("fuente"),
        nota=item.get("nota"),
        llm_veredicto=item.get("llm_veredicto"),
    )


def cargar(estado: str | None = None) -> list[Abreviatura]:
    """Todas las entradas del YAML (o solo las de un `estado`), en orden."""
    out = [_from_item(item) for item in (_raw().get("abreviaturas") or [])]
    return [a for a in out if a.estado == estado] if estado else out


def _contiguo(toks: list[str], frase: tuple[str, ...]) -> bool:
    """`frase` aparece como tokens COMPLETOS y contiguos dentro de `toks` (nunca substring)."""
    n = len(frase)
    if n == 0 or n > len(toks):
        return False
    return any(tuple(toks[i:i + n]) == frase for i in range(len(toks) - n + 1))


def expandir(descripcion: str | None) -> str:
    """Devuelve `descripcion` + los tokens de expansión de cada abreviatura aprobada presente."""
    if not descripcion:
        return descripcion or ""
    toks = norm(descripcion).split()
    if not toks:
        return descripcion
    agregar: list[str] = []
    for ab in cargar(estado="aprobada"):
        frase = ab.tokens_abrev
        if frase and _contiguo(toks, frase):
            agregar.extend(ab.tokens_expande)
    return descripcion + " " + " ".join(agregar) if agregar else descripcion


# ── Minería de candidatos (CLI `abreviaturas --buscar`) ────────────────────────────────────────

def _skeleton(word: str) -> str:
    """Primera letra + consonantes (sin vocales): 'leche' -> 'lch'."""
    if not word:
        return ""
    return word[0] + "".join(c for c in word[1:] if c not in "aeiou")


def _familia_palabras(catalog: dict) -> dict[str, set[str]]:
    """producto_id -> palabras de su nombre + sus frases `incluye` (ya tokenizadas por catalog)."""
    out: dict[str, set[str]] = {}
    for pid, prod in catalog.items():
        palabras = set(norm(prod.nombre).split())
        for frase in prod.incluye:
            palabras.update(frase)
        out[pid] = {w for w in palabras if w not in _STOPWORDS and len(w) >= 4}
    return out


def descripciones_sin_producto(conn: sqlite3.Connection) -> list[str]:
    """Descripciones distintas de renglones y observaciones de mercado sin producto asignado."""
    rows = conn.execute(
        "SELECT DISTINCT descripcion FROM renglones WHERE producto_id IS NULL AND descripcion IS NOT NULL "
        "UNION SELECT DISTINCT descripcion FROM precios_mercado WHERE producto_id IS NULL "
        "AND descripcion IS NOT NULL").fetchall()
    return [r[0] for r in rows if r[0]]


def minar_candidatos(conn: sqlite3.Connection, limite: int = 20) -> list[dict]:
    """Candidatos (token corto, palabra de producto) por prefijo o esqueleto sin vocales.

    Solo propone tokens de UNA sola palabra (los multi-palabra, como "p descr", no se pueden
    minar de forma confiable por frecuencia sola — se agregan a mano, ver config/abreviaturas.yml).
    Nunca propone un token que ya está aprobado/propuesto/rechazado, ni uno que ya aparece tal
    cual en alguna frase `incluye` del catálogo (ese caso ya matchea sin ayuda).
    """
    from . import catalog as catalog_mod
    catalog = catalog_mod.load_catalog()
    familias = _familia_palabras(catalog)
    ya_vistos = {a.abreviatura for a in cargar()}
    palabras_catalogo = {w for fam in familias.values() for w in fam}

    frecuencia: dict[str, int] = {}
    ejemplos: dict[str, list[str]] = {}
    for desc in descripciones_sin_producto(conn):
        toks = set(norm(desc).split())
        for tok in toks:
            if not (2 <= len(tok) <= 6) or tok in _STOPWORDS or tok in palabras_catalogo or tok.isdigit():
                continue
            frecuencia[tok] = frecuencia.get(tok, 0) + 1
            ejemplos.setdefault(tok, [])
            if len(ejemplos[tok]) < 3:
                ejemplos[tok].append(desc)

    candidatos: list[dict] = []
    for tok, freq in frecuencia.items():
        if tok in ya_vistos or freq < 2:
            continue
        for pid, fam in familias.items():
            for palabra in fam:
                regla = None
                if palabra.startswith(tok) and len(palabra) - len(tok) >= 2:
                    regla = "prefijo"
                elif _skeleton(tok) == _skeleton(palabra) and tok != palabra and len(palabra) > len(tok):
                    regla = "consonantes"
                if regla:
                    candidatos.append({
                        "abreviatura": tok, "expande": palabra, "producto_id": pid,
                        "regla": regla, "frecuencia": freq, "evidencia": ejemplos[tok],
                    })
    # Orden determinístico (antes: el orden de un `set` podía variar entre corridas): primero
    # frecuencia, después la palabra más larga/específica ("fideos" antes que "fideo"), después
    # alfabético para que un empate total no dependa del hash de la corrida.
    candidatos.sort(key=lambda c: (-c["frecuencia"], -len(c["expande"]), c["expande"]))
    # Un token puede matchear más de un producto (ambiguo): nos quedamos con el de mayor
    # frecuencia de aparición ya ordenado, un candidato por token.
    vistos_tok: set[str] = set()
    dedup: list[dict] = []
    for c in candidatos:
        if c["abreviatura"] in vistos_tok:
            continue
        vistos_tok.add(c["abreviatura"])
        dedup.append(c)
    return dedup[:limite]


def _llm_verificar(candidato: dict) -> dict | None:
    """Consulta al LLM barato (role=cheap) si el candidato es una abreviatura real. None si no hay LLM."""
    template = (config.PROMPTS_DIR / "abreviatura_candidata.md").read_text(encoding="utf-8")
    prompt = (template
              .replace("<<TOKEN>>", candidato["abreviatura"])
              .replace("<<PALABRA>>", candidato["expande"])
              .replace("<<PRODUCTO>>", candidato.get("producto_id") or "(varios)")
              .replace("<<EJEMPLOS>>", "\n".join(f"- {e}" for e in candidato["evidencia"])))
    schema = {
        "type": "object", "additionalProperties": False,
        "required": ["es_abreviatura", "confianza", "razon"],
        "properties": {
            "es_abreviatura": {"type": "boolean"},
            "confianza": {"type": "string", "enum": ["alta", "media", "baja"]},
            "razon": {"type": "string"},
        },
    }
    try:
        return llm.complete_json(prompt, schema, role="cheap", proposito="abreviatura_candidata",
                                 schema_name="abreviatura_candidata")
    except llm.LLMUnavailable as exc:
        logger.warning("LLM no disponible para verificar abreviatura %r: %s", candidato["abreviatura"], exc)
        return None


def proponer(conn: sqlite3.Connection, limite: int = 20, con_llm: bool = False) -> list[dict]:
    """Mina candidatos y los agrega a config/abreviaturas.yml como estado=propuesta (nunca
    aprobada, con o sin --con-llm). Devuelve la lista de entradas efectivamente agregadas
    (se salta las que ya estaban, por si se corre dos veces)."""
    candidatos = minar_candidatos(conn, limite=limite)
    ya_vistos = {a.abreviatura for a in cargar()}
    nuevas: list[dict] = []
    for c in candidatos:
        if c["abreviatura"] in ya_vistos:
            continue
        entry = {
            "abreviatura": c["abreviatura"], "expande": c["expande"], "producto_id": c["producto_id"],
            "estado": "propuesta", "evidencia": c["evidencia"], "fecha": _hoy(), "fuente": "humano",
            "nota": f"minada por 'abreviaturas --buscar' (regla: {c['regla']}, frecuencia: {c['frecuencia']})",
        }
        if con_llm:
            veredicto = _llm_verificar(c)
            if veredicto is not None:
                entry["fuente"] = "llm"
                entry["llm_veredicto"] = veredicto
                entry["nota"] += (f" · LLM dice es_abreviatura={veredicto.get('es_abreviatura')} "
                                  f"(confianza {veredicto.get('confianza')}): {veredicto.get('razon', '')}")
        nuevas.append(entry)
    if nuevas:
        _guardar(cargar(), nuevas)
    return nuevas


def aprobar(abreviatura: str, estado: str = "aprobada") -> bool:
    """Cambia el estado de una entrada existente (estado ∈ aprobada|rechazada). False si no existe."""
    items = (_raw().get("abreviaturas") or [])
    encontrado = False
    for item in items:
        if str(item.get("abreviatura")) == abreviatura:
            item["estado"] = estado
            encontrado = True
    if encontrado:
        _escribir_raw(items)
    return encontrado


def _hoy() -> str:
    import datetime as dt
    return dt.date.today().isoformat()


def _guardar(existentes: list[Abreviatura], nuevas: list[dict]) -> None:
    items = [{
        "abreviatura": a.abreviatura, "expande": a.expande, "producto_id": a.producto_id,
        "estado": a.estado, "evidencia": list(a.evidencia), "fecha": a.fecha, "fuente": a.fuente,
        **({"nota": a.nota} if a.nota else {}),
        **({"llm_veredicto": a.llm_veredicto} if a.llm_veredicto else {}),
    } for a in existentes]
    items.extend(nuevas)
    _escribir_raw(items)


def _escribir_raw(items: list[dict]) -> None:
    """Reescribe config/abreviaturas.yml preservando el comentario de cabecera (todo lo que son
    líneas '#' o vacías antes de la primera clave) para no perder la documentación del archivo
    cada vez que `proponer`/`aprobar` lo regeneran."""
    path = _yaml_path()
    header_lines: list[str] = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.startswith("#") or not line.strip():
                header_lines.append(line)
            else:
                break
    header = ("\n".join(header_lines) + "\n\n") if header_lines else ""
    body = yaml.safe_dump({"abreviaturas": items}, allow_unicode=True, sort_keys=False, default_flow_style=False)
    path.write_text(header + body, encoding="utf-8")
