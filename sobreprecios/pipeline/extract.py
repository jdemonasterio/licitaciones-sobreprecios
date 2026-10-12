"""Extrae renglones adjudicados del texto de una norma del Boletín Oficial.

Dos pasadas, de la más barata a la más cara:
  1. Regex para el formato "Renglón N, CANTIDAD (n) unidad de <desc>, marca X,
     cuyo precio unitario es de PESOS ... ($p), por un monto total de ... ($t)"
     que usan MDS y otros ministerios. Gratis y exacto cuando aplica.
  2. LLM barato (rol cheap) sobre la parte resolutiva solamente (desde
     "RESUELVE"/"DISPONE"), con schema JSON estricto.

Cada renglón se valida: si cantidad x precio_unitario no cierra con el monto
(>2%), queda marcado en `alertas` en vez de "corregirse" en silencio. Si falta
el precio unitario pero hay cantidad y monto, se calcula y se marca.

Al guardar, si el proceso tenía renglones importados del dataset Quijote 2021,
se reemplazan por los extraídos del aviso original (la comparación entre ambos
queda en el golden de validación, ver cli.py validar-extraccion).
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3

from .. import config, db, llm
from ..units import parse_number

logger = logging.getLogger(__name__)

_RESUELVE_RE = re.compile(r"\b(RESUELVE|DISPONE|DECIDE|DECRETA|RESUELVEN)\s*:", re.I)
_PROVEEDOR_RE = re.compile(
    r"(?P<prov>[A-ZÁÉÍÓÚÑÜ0-9][^\n]{2,140}?)\s*[–—-]?\s*\(?\s*C\.?\s?U\.?\s?I\.?\s?T\.?\s*(?:N\.?\s*[°º]?\.?)?\s*:?\s*"
    r"(?P<cuit>\d{2}\s?-?\s?\d{8}\s?-?\s?\d)")
_RENGLON_RE = re.compile(
    r"Rengl[oó]n\s*(?:N[°º.]?\s*)?(?P<nro>\d+)\s*[,:-]?\s*"
    r"(?:[A-ZÁÉÍÓÚÑ\s]+)?\(\s*(?P<cant>[\d.,]+)\s*\)\s*(?P<unidad>\w+)\s+(?:de\s+)?"
    r"(?P<resto>.*?)cuyo\s+precio\s+unitario\s+es\s+de\s+PESOS[^($]*\(\s*\$\s*(?P<pu>[\d.,]+)[^)]*\)"
    # "($553.-), según mejora, por un monto total": se toleran incisos cortos en el medio.
    r"[^$(]{0,80}?por\s+(?:un\s+|el\s+)?monto\s+total\s+de\s+PESOS[^($]*\(\s*\$\s*(?P<total>[\d.,]+)",
    re.I | re.S)
_MARCA_RE = re.compile(r",\s*marca\s+(?P<marca>[^,;]+?)\s*,?\s*$", re.I)
# Marca EXIGIDA por el pliego/descripción (jdemonasterio 2026-10-12, distinto de `marca`/_MARCA_RE que
# es la OFRECIDA por el ganador). Dos patrones, ambos conservadores a propósito ("un resultado falso
# es peor que ninguno" — si no está clarísimo, no se asume ninguna exigencia):
#   - "marca X" (con o sin "o equivalente/similar" después): "marca" es casi siempre, en este corpus,
#     el marcador explícito de una marca (nunca se usa para otro atributo).
#   - "tipo X o equivalente/similar": "tipo" SOLO cuenta como marca cuando viene seguido de "o
#     equivalente/similar" en la misma frase — sin eso, "tipo" casi siempre describe otro atributo
#     (ej. ficha SIByS "TIPO: GRANO LARGO-FINO", "TIPO: CON PALO"), no una marca.
#   - una sola palabra siempre; una segunda SOLO si no es un conector/token no-marca ("o", "u",
#     "equivalente", "similar", "x" de envase, o puramente numérica) — sin este freno, "GALLO O
#     EQUIVALENTE" se capturaba entero como nombre de marca y se perdía el "o equivalente" que
#     define si es exigencia estricta (bug encontrado armando los tests).
_MARCA_NOMBRE = (r"[A-Za-zÁÉÍÓÚÑáéíóúñ0-9][\w.\-]*"
                r"(?:\s+(?!(?:[oOuU]|equivalente|similar|[Xx]|\d+)\b)[A-Za-zÁÉÍÓÚÑáéíóúñ0-9][\w.\-]*)?")
_MARCA_EXIGIDA_RE = re.compile(rf"\bmarca\s*:?\s+(?P<marca>{_MARCA_NOMBRE})", re.I)
_TIPO_EXIGIDO_EQUIV_RE = re.compile(
    rf"\btipo\s*:?\s+(?P<marca>{_MARCA_NOMBRE})\s+[oOuU]\s+(?:equivalente|similar)s?\b", re.I)
_MARCA_EQUIVALENTE_RE = re.compile(r"\b[oOuU]\s+(?:equivalente|similar)s?\b", re.I)


def parse_marca_exigida(descripcion: str | None) -> tuple[str | None, bool | None]:
    """(marca_exigida, marca_equivalente_ok) a partir del texto del renglón/pliego.

    `marca_equivalente_ok`:
      - False: el texto dice "marca X" SIN "o equivalente/similar" cerca -> exigencia estricta
        (pipeline.compare.compare() puede usar `misma_marca` como referencia PRINCIPAL).
      - True: dice "marca X o equivalente/similar" o "tipo X o equivalente/similar" -> el pliego
        acepta otra marca, no es exigencia estricta (la marca ofrecida por el ganador queda solo
        como contexto, nunca referencia principal).
      - None (con marca_exigida None): no se encontró ninguno de los dos patrones — no se asume nada.
    Ventana corta (40 caracteres después del nombre) para decidir si hay "o equivalente/similar" tras
    "marca X": ni todo el renglón (podría hablar de otra cosa) ni solo la palabra siguiente.
    """
    if not descripcion:
        return None, None
    m = _MARCA_EXIGIDA_RE.search(descripcion)
    if m:
        marca = m.group("marca").strip().rstrip(",.;:")
        ventana = descripcion[m.end():m.end() + 40]
        return marca, bool(_MARCA_EQUIVALENTE_RE.search(ventana))
    m = _TIPO_EXIGIDO_EQUIV_RE.search(descripcion)
    if m:
        return m.group("marca").strip().rstrip(",.;:"), True
    return None, None
_ENVASE_RE = re.compile(r"contenido\s+neto\s+de\s+(?:[A-ZÁÉÍÓÚÑ\s]+)?\(?\s*([\d.,]+)\s*\)?\s*(kilogramos?|gramos?|litros?|mililitros?|cc|kg|g|l)\b", re.I)
_PROCESO_RE = re.compile(r"\b(\d{1,3}-\d{4}-[A-Z]{2,4}\d{2})\b")
_TIPO_RE = [
    ("licitacion_publica", re.compile(r"licitaci[oó]n\s+p[uú]blica", re.I)),
    ("licitacion_privada", re.compile(r"licitaci[oó]n\s+privada", re.I)),
    ("emergencia", re.compile(r"emergencia", re.I)),
    ("contratacion_directa", re.compile(r"contrataci[oó]n\s+directa", re.I)),
    ("compulsa", re.compile(r"compulsa", re.I)),
    ("adjudicacion_simple", re.compile(r"adjudicaci[oó]n\s+simple", re.I)),
]

_RENGLON_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["renglon", "descripcion", "cantidad", "unidad", "contenido_envase", "marca",
                 "precio_unitario", "monto_total"],
    "properties": {
        "renglon": {"type": ["integer", "null"]},
        "descripcion": {"type": ["string", "null"]},
        "cantidad": {"type": ["number", "null"]},
        "unidad": {"type": ["string", "null"]},
        "contenido_envase": {"type": ["string", "null"]},
        "marca": {"type": ["string", "null"]},
        "precio_unitario": {"type": ["number", "null"]},
        "monto_total": {"type": ["number", "null"]},
    },
}

# Por adjudicatario y no por renglón suelto: muchas normas informan UN monto por
# proveedor para varios renglones ("Renglón 1 por 321.144 kg, renglón 3 por
# 115.072 kg y renglón 5 por 365.551 kg: $309.255.655"). Ver flatten_llm().
LLM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["es_adjudicacion", "numero_proceso", "tipo_procedimiento", "objeto", "categoria",
                 "adjudicatarios"],
    "properties": {
        "es_adjudicacion": {"type": "boolean"},
        "numero_proceso": {"type": ["string", "null"]},
        "tipo_procedimiento": {"type": ["string", "null"]},
        "objeto": {"type": ["string", "null"]},
        "categoria": {"type": "string", "enum": ["bienes_consumo", "medicamentos_insumos_medicos",
                                                 "bienes_durables", "servicios", "obras", "otros"]},
        "adjudicatarios": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["proveedor", "cuit", "monto_total_adjudicado", "renglones"],
                "properties": {
                    "proveedor": {"type": ["string", "null"]},
                    "cuit": {"type": ["string", "null"]},
                    "monto_total_adjudicado": {"type": ["number", "null"]},
                    "renglones": {"type": "array", "items": _RENGLON_SCHEMA},
                },
            },
        },
    },
}


def flatten_llm(adjudicatarios: list[dict]) -> list[dict]:
    """Adjudicatarios -> renglones planos con proveedor/CUIT.

    Si un proveedor tiene un monto conjunto y sus renglones no traen precio ni
    monto, y todos están en la misma unidad, se reparte con un precio promedio
    del grupo (total / suma de cantidades) y se deja la alerta: el número es un
    promedio ponderado, no el precio de cada renglón.
    """
    rows: list[dict] = []
    for adj in adjudicatarios or []:
        renglones = adj.get("renglones") or []
        total = adj.get("monto_total_adjudicado")
        sin_precio = [r for r in renglones if r.get("precio_unitario") is None and r.get("monto_total") is None]
        unidades = {(r.get("unidad") or "").lower() for r in renglones}
        # Repartir un monto conjunto solo tiene sentido si es EL MISMO bien en todos los renglones
        # (riesgo señalado por el LLM prime: misma unidad no alcanza, fabrica precios).
        from ..textnorm import norm as _norm
        descripciones = {_norm(r.get("descripcion")) for r in renglones}
        cantidad_total = sum(r.get("cantidad") or 0 for r in renglones)
        promedio = None
        if (total and sin_precio and len(sin_precio) == len(renglones) and len(unidades) == 1
                and len(descripciones) == 1 and cantidad_total):
            promedio = total / cantidad_total
        for r in renglones:
            row = dict(r, proveedor=adj.get("proveedor"), cuit=adj.get("cuit"))
            row["_alertas_previas"] = []
            if promedio is not None:
                row["precio_unitario"] = round(promedio, 4)
                row["monto_total"] = round(promedio * (r.get("cantidad") or 0), 2)
                if len(renglones) > 1:
                    nros = ", ".join(str(x.get("renglon")) for x in renglones)
                    row["_alertas_previas"].append(
                        f"precio_promedio_del_grupo: el aviso informa un monto conjunto (${total:,.0f}) "
                        f"para los renglones {nros}")
                else:
                    row["_alertas_previas"].append("precio_unitario_calculado: monto_total / cantidad")
            elif len(renglones) == 1 and total and r.get("monto_total") is None:
                row["monto_total"] = total
            elif total and len(renglones) > 1 and r.get("monto_total") is None:
                nros = ", ".join(str(x.get("renglon")) for x in renglones)
                row["_alertas_previas"].append(
                    f"monto_conjunto: ${total:,.0f} para los renglones {nros} (sin cantidades en el aviso)")
            rows.append(row)
    return rows


def resolutive_part(texto: str, max_chars: int = 30000) -> str:
    """Desde 'RESUELVE:' en adelante (ahí está lo adjudicado); todo si no aparece."""
    match = _RESUELVE_RE.search(texto or "")
    part = texto[match.start():] if match else (texto or "")
    return part[:max_chars]


def tipo_procedimiento(texto: str) -> str | None:
    head = (texto or "")[:6000]
    for name, rx in _TIPO_RE:
        if rx.search(head):
            return name
    return None


_PROCESO_LEGAL_RE = re.compile(
    r"((?:Licitaci[oó]n\s+(?:P[uú]blica|Privada)|Contrataci[oó]n\s+(?:Directa|por\s+Emergencia(?:\s+COVID-19)?)"
    r"|Compulsa\s+Abreviada|Adjudicaci[oó]n\s+Simple)\s+N\.?\s*[°º]?\s*\d+\s*/\s*\d{2,4})", re.I)


def numero_proceso(texto: str) -> str | None:
    """'80-0014-LPU25' (formato COMPR.AR) o, si no, 'Licitación Pública N° 12/21'."""
    match = _PROCESO_RE.search(texto or "")
    if match:
        return match.group(1)
    legal = _PROCESO_LEGAL_RE.search(texto or "")
    return re.sub(r"\s+", " ", legal.group(1)) if legal else None


def regex_extract(texto: str) -> list[dict]:
    """Renglones en el formato 'Renglón N, CANT (n) unidad ... precio unitario ... monto total'.

    Asigna a cada renglón el último proveedor+CUIT que apareció antes en el texto.
    """
    part = resolutive_part(texto)
    providers = [(m.start(), m.group("prov").strip(" :–-"), re.sub(r"\D", "", m.group("cuit")))
                 for m in _PROVEEDOR_RE.finditer(part)]
    out = []
    for m in _RENGLON_RE.finditer(part):
        prov = [p for p in providers if p[0] < m.start()]
        proveedor, cuit = (prov[-1][1], prov[-1][2]) if prov else (None, None)
        if proveedor:
            proveedor = re.sub(r"^.*?(?:firmas?|siguientes?)\s*[:,]?\s*", "", proveedor, flags=re.I) or proveedor
            # En párrafos corridos el nombre viene después del monto del proveedor anterior:
            # "...($35.364.000.-). COPACABANA S.A." -> "COPACABANA S.A."
            proveedor = re.split(r"\)\s*[.,;]?\s+|:\s+", proveedor)[-1].strip(" ,;") or proveedor
        resto = re.sub(r"\s+", " ", m.group("resto")).strip(" ,")
        marca_m = _MARCA_RE.search(resto)
        marca = marca_m.group("marca").strip() if marca_m else None
        descripcion = resto[:marca_m.start()].strip(" ,") if marca_m else resto
        envase_m = _ENVASE_RE.search(resto)
        contenido = f"{envase_m.group(1)} {envase_m.group(2)}" if envase_m else None
        descripcion = re.sub(r",?\s*en envases.*$", "", descripcion, flags=re.I).strip(" ,")
        out.append({
            "proveedor": proveedor,
            "cuit": f"{cuit[:2]}-{cuit[2:10]}-{cuit[10:]}" if cuit and len(cuit) == 11 else cuit,
            "renglon": int(m.group("nro")),
            "descripcion": descripcion,
            "cantidad": parse_number(m.group("cant")),
            "unidad": m.group("unidad").lower(),
            "contenido_envase": contenido,
            "marca": marca,
            "precio_unitario": parse_number(m.group("pu")),
            "monto_total": parse_number(m.group("total")),
        })
    return out


def validate_renglon(r: dict) -> list[str]:
    """Chequea consistencia; completa precio_unitario si falta. Devuelve alertas."""
    alertas = []
    cant, pu, total = r.get("cantidad"), r.get("precio_unitario"), r.get("monto_total")
    if pu is None and cant and total:
        r["precio_unitario"] = round(total / cant, 4)
        alertas.append("precio_unitario_calculado: monto_total / cantidad")
    elif cant and pu and total:
        calc = cant * pu
        if abs(calc - total) / total > 0.02:
            alertas.append(f"monto_inconsistente: cantidad x precio = {calc:,.0f} vs monto {total:,.0f}")
    if not r.get("descripcion"):
        alertas.append("sin_descripcion: el texto solo cita el número de renglón (buscar en COMPR.AR)")
    return alertas


def llm_extract(texto: str, organismo: str, norma: str, conn: sqlite3.Connection | None = None) -> dict:
    template = (config.PROMPTS_DIR / "extraer_adjudicacion.md").read_text(encoding="utf-8")
    prompt = (template.replace("<<ORGANISMO>>", organismo or "?")
              .replace("<<NORMA>>", norma or "?")
              .replace("<<TEXTO>>", resolutive_part(texto)))
    return llm.complete_json(prompt, LLM_SCHEMA, role="cheap", proposito="extraer_adjudicacion",
                             conn=conn, schema_name="adjudicacion")


def extract_document(texto: str, organismo: str = "", norma: str = "",
                     conn: sqlite3.Connection | None = None, use_llm: bool = True) -> dict:
    """Extracción completa de un aviso: metadata del proceso + renglones validados."""
    result = {
        "numero_proceso": numero_proceso(texto),
        "tipo_procedimiento": tipo_procedimiento(texto),
        "objeto": None,
        "categoria": None,
        "es_adjudicacion": None,
        "renglones": [],
        "metodo": None,
    }
    regex_rows = regex_extract(texto)
    if regex_rows:
        result.update(renglones=regex_rows, metodo="regex", es_adjudicacion=True)
    elif use_llm:
        try:
            data = llm_extract(texto, organismo, norma, conn)
            result.update(
                numero_proceso=data.get("numero_proceso") or result["numero_proceso"],
                tipo_procedimiento=result["tipo_procedimiento"] or data.get("tipo_procedimiento"),
                objeto=data.get("objeto"), categoria=data.get("categoria"),
                es_adjudicacion=data.get("es_adjudicacion"),
                renglones=flatten_llm(data.get("adjudicatarios") or []),
                monto_adjudicado=sum(a.get("monto_total_adjudicado") or 0
                                     for a in data.get("adjudicatarios") or []) or None,
                metodo=f"llm:{llm.resolve_model('cheap')}")
        except llm.LLMUnavailable as exc:
            result["metodo"] = f"sin_extraccion ({exc})"
    for row in result["renglones"]:
        row["alertas"] = row.pop("_alertas_previas", []) + validate_renglon(row)
    return result


def _row_key(r: dict) -> str:
    base = f"{r.get('cuit') or r.get('proveedor')}|{r.get('renglon')}|{r.get('descripcion')}|{r.get('cantidad')}"
    return "bo:" + hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def store_extraction(conn: sqlite3.Connection, doc: sqlite3.Row, extraction: dict) -> int:
    """Upsert del proceso + renglones extraídos. Devuelve cuántos renglones guardó."""
    titulo = doc["titulo"] or ""
    organismo = titulo.split(" - ")[0].strip() if " - " in titulo else titulo
    proceso_id = db.upsert(conn, "procesos", {
        "fuente": doc["fuente"],
        "external_id": doc["external_id"],
        "numero": extraction.get("numero_proceso"),
        "organismo": organismo or None,
        "tipo_procedimiento": extraction.get("tipo_procedimiento"),
        "fecha_publicacion": doc["fecha_publicacion"],
        "fecha_adjudicacion": doc["fecha_publicacion"],
        "url": doc["url"],
        "documento_id": doc["id"],
        "updated_at": db.now_iso(),
    }, ("fuente", "external_id"))
    conn.execute("UPDATE procesos SET created_at=COALESCE(created_at, ?) WHERE id=?", (db.now_iso(), proceso_id))
    if extraction.get("objeto"):
        conn.execute("UPDATE procesos SET objeto=COALESCE(objeto, ?) WHERE id=?",
                     (extraction["objeto"], proceso_id))
    if extraction.get("categoria"):
        conn.execute("UPDATE procesos SET categoria=?, categoria_razon=? WHERE id=?",
                     (extraction["categoria"], f"LLM ({extraction['metodo']})", proceso_id))
    rows = extraction.get("renglones") or []
    if not rows:
        return 0
    # Re-extraer reemplaza todo lo extraído antes para este proceso (y los renglones
    # importados de Quijote 2021): la fuente de verdad es el aviso original.
    conn.execute("DELETE FROM renglones WHERE proceso_id=?", (proceso_id,))
    for r in rows:
        descripcion_renglon = r.get("descripcion") or f"renglón {r.get('renglon')} (sin descripción en el BO)"
        marca_exigida, marca_equivalente_ok = parse_marca_exigida(descripcion_renglon)
        db.upsert(conn, "renglones", {
            "proceso_id": proceso_id,
            "nro_renglon": r.get("renglon"),
            "descripcion": descripcion_renglon,
            "cantidad": r.get("cantidad"),
            "unidad_compra": r.get("unidad"),
            "precio_unitario": r.get("precio_unitario"),
            "monto_total": r.get("monto_total"),
            "proveedor": r.get("proveedor"),
            "proveedor_cuit": r.get("cuit"),
            "marca": r.get("marca"),
            "marca_exigida": marca_exigida,
            "marca_equivalente_ok": marca_equivalente_ok,
            "match_razon": None,
            "alertas": json.dumps(r.get("alertas") or [], ensure_ascii=False) or None,
            "extraccion_metodo": extraction.get("metodo"),
            "external_key": _row_key(r),
        }, ("proceso_id", "external_key"))
        # El contenido del envase se guarda en la descripción si no estaba, para que el
        # normalizador de tamaños lo encuentre.
        if r.get("contenido_envase") and r.get("descripcion") and r["contenido_envase"] not in r["descripcion"]:
            conn.execute("UPDATE renglones SET descripcion = descripcion || ' x ' || ? "
                         "WHERE proceso_id=? AND external_key=?",
                         (r["contenido_envase"], proceso_id, _row_key(r)))
    total = sum((r.get("monto_total") or 0) for r in rows)
    # Si los renglones no traen monto (adjudicación por grupo de renglones sin cantidades),
    # el total del proceso sale de los montos por adjudicatario.
    total = max(total, extraction.get("monto_adjudicado") or 0)
    conn.execute("UPDATE procesos SET monto_total=? WHERE id=?", (total or None, proceso_id))
    return len(rows)


def run(conn: sqlite3.Connection, only_pending: bool = True, use_llm: bool = True,
        progress=None) -> str:
    """Extrae todos los documentos del BO (primera sección) todavía sin renglones extraídos."""
    llm.reset_budget()
    docs = conn.execute(
        "SELECT d.* FROM documentos d WHERE d.fuente='bo_primera'"
        + (" AND NOT EXISTS (SELECT 1 FROM procesos p JOIN renglones r ON r.proceso_id=p.id "
           "WHERE p.fuente=d.fuente AND p.external_id=d.external_id "
           "AND r.extraccion_metodo IS NOT NULL AND r.extraccion_metodo <> 'legacy_quijotes')"
           if only_pending else "")).fetchall()
    n_docs = n_rows = 0
    metodos: dict[str, int] = {}
    for i, doc in enumerate(docs, 1):
        titulo = doc["titulo"] or ""
        organismo, _, norma = titulo.partition(" - ")
        extraction = extract_document(doc["texto"] or "", organismo, norma, conn, use_llm=use_llm)
        n = store_extraction(conn, doc, extraction)
        conn.commit()
        n_docs += 1
        n_rows += n
        key = (extraction.get("metodo") or "?").split(" ")[0]
        metodos[key] = metodos.get(key, 0) + 1
        if progress:
            progress(f"extrayendo {i}/{len(docs)}")
    spend = llm.run_spend()
    return (f"{n_docs} avisos procesados, {n_rows} renglones extraídos "
            f"({', '.join(f'{k}: {v}' for k, v in metodos.items()) or 'nada'}); LLM USD {spend:.4f}")
