"""Grafo de sucesión institucional entre organismos (MEJORAS #39, P13).

Por qué: `config/organismos.yml` ya registra renombres a mano (`renombres:`, con
`verificar: true` cuando no están confirmados con una norma) pero el sistema
NUNCA los une automáticamente a un ranking histórico (MEJORAS #14 es la vista
que lo mostraría): agrupar mal un organismo renombrado inflaría o escondería
sobreprecios de gestiones distintas. Este módulo busca en la prensa
confirmaciones INDEPENDIENTES de cada cambio institucional (nombre del SAF
anterior -> nombre nuevo) antes de dejarlo como "confirmado" en la tabla
`organismos_sucesion`; con menos de 4 fuentes independientes queda "propuesto"
(nunca se borra ni se inventa una confirmación que no se encontró).

Flujo:
  config/organismos.yml (`renombres`, semilla a mano)
    -> buscar_noticias()      Google News RSS (sin API key), unas pocas queries
    -> _llm_confirma()        LLM barato (role=cheap): ¿esta nota confirma el cambio?
    -> guardar()              organismos_sucesion, rechaza el arco si cierra un ciclo

Es de SOLO LECTURA para el resto del sistema: nada en `pipeline/` ni `normalize/`
agrupa organismos por este grafo todavía (eso es MEJORAS #14, a futuro);
`leer()` es la función de consulta que esa vista usaría.

Independencia: Google News no expone el dominio real del medio sin resolver el
link de redirección (`news.google.com/rss/articles/...`) uno por uno — usamos
el nombre del medio (`<source>` del RSS) como proxy de "fuente independiente".
Documentado como decisión, no como garantía perfecta (dos medios con el mismo
nombre de fuente rarísimo, pero el mismo medio con secciones distintas podría
repetirse — por eso 4 es el piso, no una propiedad matemática).
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

import defusedxml.ElementTree as ET   # el RSS es contenido externo no confiable (XXE/billion-laughs)
import httpx

from . import config, db, llm

logger = logging.getLogger(__name__)

NEWS_RSS_URL = "https://news.google.com/rss/search"
USER_AGENT = "Mozilla/5.0 (compatible; transparencia-compras/0.1; investigacion de organismos publicos)"
DELAY_S = 1.0
MIN_CONFIRMACIONES_INDEPENDIENTES = 4


@dataclass
class Seed:
    saf: str
    anterior: str
    nuevo: str
    desde: str | None


def seeds() -> list[Seed]:
    """(saf, anterior, nuevo, desde) desde config/organismos.yml `renombres`.

    `anterior` es `nombres[0]` (el comentario del YAML es explícito: "la primera
    [de `nombres`] es el nombre a mostrar", el nombre de referencia del SAF).
    Ej. SAF 311: anterior="Ministerio de Desarrollo Social", nuevo="Ministerio
    de Capital Humano" — exactamente el ejemplo de MEJORAS #39.
    """
    data = (config.load_yaml("organismos.yml").get("organismos") or {})
    out: list[Seed] = []
    for saf, info in data.items():
        nombres = info.get("nombres") or []
        if not nombres:
            continue
        for ren in info.get("renombres") or []:
            nuevo = ren.get("nombre")
            if nuevo:
                out.append(Seed(saf=str(saf), anterior=nombres[0], nuevo=nuevo, desde=ren.get("desde")))
    return out


def buscar_noticias(query: str, max_resultados: int = 6) -> list[dict]:
    """Google News RSS (sin clave): título, medio (`<source>`, proxy de dominio), fecha, link."""
    time.sleep(DELAY_S)   # cortesía con la infraestructura pública (mismo criterio que bo_sociedades)
    try:
        resp = httpx.get(NEWS_RSS_URL, params={"q": query, "hl": "es-419", "gl": "AR"}, timeout=15,
                         follow_redirects=True, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("búsqueda de noticias falló para %r: %s", query, exc)
        return []
    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("RSS inválido para %r: %s", query, exc)
        return []
    out = []
    for item in root.findall(".//item")[:max_resultados]:
        source = item.find("source")
        out.append({
            "titulo": (item.findtext("title") or "").strip(),
            "medio": (source.text if source is not None else "").strip(),
            "fecha_nota": (item.findtext("pubDate") or "").strip(),
            "url": (item.findtext("link") or "").strip(),
        })
    return out


_SCHEMA_CONFIRMACION = {
    "type": "object", "additionalProperties": False, "required": ["confirma", "tipo", "fecha", "razon"],
    "properties": {
        "confirma": {"type": "boolean"},
        "tipo": {"type": ["string", "null"], "enum": ["renombre", "absorcion", "division", None]},
        "fecha": {"type": ["string", "null"]},
        "razon": {"type": "string"},
    },
}


def _llm_confirma(anterior: str, nuevo: str, item: dict) -> dict | None:
    """None si no hay LLM disponible (el caller decide: queda sin confirmar, nunca se asume)."""
    template = (config.PROMPTS_DIR / "organismo_sucesion.md").read_text(encoding="utf-8")
    prompt = (template.replace("<<ANTERIOR>>", anterior).replace("<<NUEVO>>", nuevo)
              .replace("<<TITULO>>", item.get("titulo", "")).replace("<<MEDIO>>", item.get("medio", ""))
              .replace("<<FECHA_NOTA>>", item.get("fecha_nota", "")))
    try:
        return llm.complete_json(prompt, _SCHEMA_CONFIRMACION, role="cheap", proposito="organismo_sucesion",
                                 schema_name="organismo_sucesion")
    except llm.LLMUnavailable as exc:
        logger.warning("LLM no disponible para verificar %r -> %r: %s", anterior, nuevo, exc)
        return None


def _consultas(anterior: str, nuevo: str, desde: str | None) -> list[str]:
    """Queries para Google News. Con fecha del cambio, se acota a esa ventana (after:/before:): sin eso, el RSS
    devuelve noticias recientes que solo MENCIONAN el organismo nuevo (verificado 2026-10-12: 6 de 6 resultados
    para Capital Humano eran de 2026 y ninguno hablaba de su creación)."""
    import datetime as dt
    corto_ant = anterior.replace("Ministerio de ", "")
    corto_nuevo = nuevo.replace("Ministerio de ", "")
    base = [f'"{anterior}" "{nuevo}"', f"{anterior} pasa a llamarse {nuevo}",
            f'"{nuevo}" absorbe "{corto_ant}"', f'se crea el "{nuevo}" "{corto_ant}"',
            f'decreto "{nuevo}" "{corto_ant}"', f'"{corto_nuevo}" reemplaza "{corto_ant}"',
            f'superministerio "{corto_nuevo}" "{corto_ant}"', f'"{corto_nuevo}" unifica "{corto_ant}"',
            f'"{corto_ant}" pasa a "{corto_nuevo}"']
    if not desde:
        return base
    d = dt.date.fromisoformat(desde[:10])
    ventana = f" after:{(d - dt.timedelta(days=45)).isoformat()} before:{(d + dt.timedelta(days=120)).isoformat()}"
    return [q + ventana for q in base]


def investigar(anterior: str, nuevo: str, con_llm: bool = True, max_resultados: int = 6,
               desde: str | None = None) -> dict:
    """Busca noticias y (si `con_llm`) les pide al LLM barato que confirme el cambio institucional.

    Devuelve {anterior, nuevo, n_resultados, confirmaciones: [...], medios_independientes: [...],
    estado}. `estado` es "confirmado" solo con >= MIN_CONFIRMACIONES_INDEPENDIENTES medios
    DISTINTOS confirmando; si no, "propuesto" con lo que se encontró (nunca se descarta).
    """
    queries = _consultas(anterior, nuevo, desde)
    vistos_url: set[str] = set()
    items: list[dict] = []
    for q in queries:
        for it in buscar_noticias(q, max_resultados=max_resultados):
            if it["url"] and it["url"] not in vistos_url:
                vistos_url.add(it["url"])
                items.append(it)
    confirmaciones: list[dict] = []
    for it in items:
        veredicto = _llm_confirma(anterior, nuevo, it) if con_llm else None
        if veredicto and veredicto.get("confirma"):
            confirmaciones.append({**it, **veredicto})
    medios = sorted({c["medio"] for c in confirmaciones if c.get("medio")})
    estado = "confirmado" if len(medios) >= MIN_CONFIRMACIONES_INDEPENDIENTES else "propuesto"
    return {
        "anterior": anterior, "nuevo": nuevo, "n_resultados": len(items),
        "confirmaciones": confirmaciones, "medios_independientes": medios, "estado": estado,
    }


def _alcanzable(conn, origen: str, destino: str) -> bool:
    """Hay un camino `origen -> ... -> destino` en los arcos ya guardados."""
    vistos = {origen}
    frontera = [origen]
    while frontera:
        actual = frontera.pop()
        for row in conn.execute("SELECT hacia FROM organismos_sucesion WHERE desde=?", (actual,)):
            siguiente = row["hacia"]
            if siguiente == destino:
                return True
            if siguiente not in vistos:
                vistos.add(siguiente)
                frontera.append(siguiente)
    return False


def es_aciclico(conn, desde: str, hacia: str) -> bool:
    """True si agregar el arco `desde -> hacia` NO cierra un ciclo (nunca hacia ya alcanza a desde)."""
    if desde == hacia:
        return False
    return not _alcanzable(conn, hacia, desde)


def guardar(conn, saf: str | None, resultado: dict) -> str:
    """Guarda (o actualiza) el arco de `resultado` en organismos_sucesion. Rechaza ciclos."""
    desde, hacia = resultado["anterior"], resultado["nuevo"]
    if not es_aciclico(conn, desde, hacia):
        logger.warning("arco %r -> %r rechazado: cerraría un ciclo", desde, hacia)
        return f"{desde} -> {hacia}: RECHAZADO (cerraría un ciclo con arcos ya guardados)"
    tipos = {c.get("tipo") for c in resultado["confirmaciones"] if c.get("tipo")}
    tipo = next(iter(tipos)) if len(tipos) == 1 else "renombre"
    fechas = sorted(c["fecha"] for c in resultado["confirmaciones"] if c.get("fecha"))
    fecha = fechas[0] if fechas else None
    fuentes = [{"url": c.get("url"), "medio": c.get("medio"), "titulo": c.get("titulo"),
               "fecha_nota": c.get("fecha_nota")} for c in resultado["confirmaciones"]]
    conn.execute(
        "INSERT INTO organismos_sucesion (saf, desde, hacia, tipo, fecha, fuentes_json, estado, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(desde, hacia) DO UPDATE SET saf=excluded.saf, tipo=excluded.tipo, fecha=excluded.fecha, "
        "fuentes_json=excluded.fuentes_json, estado=excluded.estado",
        (saf, desde, hacia, tipo, fecha, json.dumps(fuentes, ensure_ascii=False), resultado["estado"],
         db.now_iso()))
    conn.commit()
    return (f"{desde} -> {hacia}: {resultado['estado']} "
            f"({len(fuentes)} fuentes, {len(resultado['medios_independientes'])} medios distintos)")


def run(conn, con_llm: bool = True, max_resultados: int = 6, solo: list[Seed] | None = None) -> str:
    """Corre `investigar` + `guardar` para cada seed de config/organismos.yml (o `solo`, para tests)."""
    msgs = []
    for s in (solo if solo is not None else seeds()):
        resultado = investigar(s.anterior, s.nuevo, con_llm=con_llm, max_resultados=max_resultados, desde=s.desde)
        msgs.append(guardar(conn, s.saf, resultado))
    return " · ".join(msgs) if msgs else "sin seeds en config/organismos.yml (ninguna entrada con 'renombres')"


def leer(conn, estado: str | None = None) -> list[dict]:
    """Grafo de sucesión, SOLO LECTURA (para la agrupación histórica de MEJORAS #14, a futuro)."""
    query = "SELECT * FROM organismos_sucesion"
    args: tuple = ()
    if estado:
        query += " WHERE estado = ?"
        args = (estado,)
    return [dict(row) for row in conn.execute(query + " ORDER BY desde", args)]
