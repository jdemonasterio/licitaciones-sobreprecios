"""Gateway LLM mínimo: respuestas JSON con schema, cache en disco, costo en la base.

Por qué así:
- Todo lo que procesa este sistema es información PÚBLICA (Boletín Oficial,
  precios publicados). No hay datos de clientes ni personales sensibles, así que
  los planes GRATUITOS de los proveedores sirven, aunque usen los prompts para
  entrenar. Y el usuario puede esperar: un análisis que tarda horas está bien.
  Por eso hay dos perfiles (settings.yml llm.perfil o LIC_LLM_PERFIL):
    gratis  modelos free tier, con límite de pedidos por minuto y espera
            paciente ante "429 demasiados pedidos" (minutos, no segundos).
    pago    modelos baratos de pago, rápidos.
- Barato primero. El LLM entra solo donde las reglas no alcanzan. Cada respuesta
  se cachea por hash de (modelo, prompt, schema, archivo): re-correr no repaga.
- Tope duro por corrida (llm.max_usd_por_corrida, solo cuenta en perfil pago).
- Fallar rápido en errores no recuperables (400, clave inválida, cuota diaria
  agotada): reintentar eso solo quema tiempo. La corrida queda a medias y la
  próxima sigue desde lo pendiente (todos los pasos procesan solo lo que falta).
- Roles, no modelos: `cheap` (texto corto, mucho volumen) y `pdf` (lee PDF).
  Override por corrida: LIC_LLM_ROLE_CHEAP=<modelo>.
- Proveedores por prefijo del modelo: gemini-* (Google AI Studio, GEMINI_API_KEY),
  openrouter/<modelo> (OPENROUTER_API_KEY, OpenAI-compatible), el resto OpenAI
  (OPENAI_API_KEY).
- LIC_LLM_OFFLINE=1 desactiva todo LLM: el sistema sigue con reglas.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from . import config, db

logger = logging.getLogger(__name__)

# USD por millón de tokens (input, output). Precios de lista 2026-10; free tier = 0.
PRICES_USD_PER_MTOK = {
    "gpt-6-luna": (0.10, 0.50),
    "gpt-6-astra": (10.00, 50.00),
    "gpt-6.1-sol": (2.00, 10.00),
    "gpt-5.4-mini": (0.25, 2.00),
    "gemini-3.6-flash": (0.30, 2.50),   # verificar contra la consola de AI Studio
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-flash-lite": (0.10, 0.40),
}
_DEFAULT_PRICE = (1.0, 5.0)   # desconocido: estimar alto para que el tope corte antes
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMUnavailable(RuntimeError):
    """No hay LLM disponible (offline, sin clave, error no recuperable)."""


class BudgetExceeded(LLMUnavailable):
    """Se alcanzó el tope de gasto de la corrida."""


class QuotaExhausted(LLMUnavailable):
    """Cuota diaria del plan agotada: seguir mañana (lo pendiente queda pendiente)."""


_SPEND_LOCK = threading.Lock()
_RUN_SPEND = {"usd": 0.0}
_THROTTLE_LOCK = threading.Lock()
_LAST_CALL: dict[str, float] = {}


def reset_budget() -> None:
    with _SPEND_LOCK:
        _RUN_SPEND["usd"] = 0.0


def run_spend() -> float:
    with _SPEND_LOCK:
        return _RUN_SPEND["usd"]


def _llm_settings() -> dict:
    return config.settings().get("llm") or {}


def perfil() -> str:
    return os.getenv("LIC_LLM_PERFIL") or _llm_settings().get("perfil", "gratis")


def _perfil_cfg() -> dict:
    cfg = _llm_settings()
    return (cfg.get("perfiles") or {}).get(perfil()) or {}


def resolve_model(role: str) -> str:
    env = os.getenv(f"LIC_LLM_ROLE_{role.upper()}")
    if env:
        return env
    roles = _perfil_cfg().get("roles") or _llm_settings().get("roles") or {}
    return roles.get(role, role)


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    if perfil() == "gratis":
        return 0.0
    pin, pout = PRICES_USD_PER_MTOK.get(model, _DEFAULT_PRICE)
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


def _throttle(model: str) -> None:
    """Respeta pedidos-por-minuto del perfil (llm.perfiles.<perfil>.rpm.<modelo>)."""
    rpm = (_perfil_cfg().get("rpm") or {}).get(model)
    if not rpm:
        return
    interval = 60.0 / float(rpm)
    with _THROTTLE_LOCK:
        wait = _LAST_CALL.get(model, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _LAST_CALL[model] = time.monotonic()


def _cache_key(model: str, prompt: str, schema: dict, file_path: Path | None) -> str:
    digest = hashlib.sha256()
    digest.update(model.encode())
    digest.update(prompt.encode("utf-8"))
    digest.update(json.dumps(schema, sort_keys=True).encode())
    if file_path:
        digest.update(hashlib.sha256(Path(file_path).read_bytes()).digest())
    return digest.hexdigest()


def _log_call(conn: sqlite3.Connection | None, **row) -> None:
    if conn is None:
        return
    try:
        conn.execute(
            "INSERT INTO llm_calls (ts, model, proposito, input_tokens, output_tokens, costo_usd, "
            "cache_hit, prompt_hash, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (db.now_iso(), row.get("model"), row.get("proposito"), row.get("input_tokens", 0),
             row.get("output_tokens", 0), row.get("costo_usd", 0.0), int(row.get("cache_hit", False)),
             row.get("prompt_hash"), row.get("error")))
        conn.commit()
    except sqlite3.Error as exc:   # el log de costos nunca tumba el pipeline
        logger.warning("no se pudo loguear llm_call: %s", exc)


def _status(exc: Exception) -> int | None:
    for attr in ("status_code", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return getattr(getattr(exc, "response", None), "status_code", None)


def classify_error(exc: Exception) -> str:
    """'retry' (429/5xx/red), 'quota' (cuota diaria o de facturación) o 'fatal' (4xx)."""
    status = _status(exc)
    text = str(exc).lower()
    if ("insufficient_quota" in text or "exceeded your current quota" in text or "billing" in text
            or re.search(r"per ?day|perday|daily", text)):
        return "quota"
    if status == 429 or "resource_exhausted" in text or "rate limit" in text:
        return "retry"
    if status in {400, 401, 403, 404, 413, 422}:
        return "fatal"
    return "retry"


def _call_openai(model: str, prompt: str, schema: dict, schema_name: str, effort: str = "low") -> tuple[str, int, int]:
    import openai
    if not os.getenv("OPENAI_API_KEY"):
        raise LLMUnavailable("OPENAI_API_KEY no está definida en .env")
    client = openai.OpenAI(timeout=120, max_retries=0)
    resp = client.responses.create(
        model=model,
        input=prompt,
        reasoning={"effort": effort},
        text={"format": {"type": "json_schema", "name": schema_name, "schema": schema, "strict": True}},
    )
    usage = getattr(resp, "usage", None)
    return (resp.output_text, getattr(usage, "input_tokens", 0) or 0,
            getattr(usage, "output_tokens", 0) or 0)


def _call_openrouter(model: str, prompt: str, schema: dict, schema_name: str) -> tuple[str, int, int]:
    """OpenRouter (OpenAI-compatible). Los modelos ':free' no cuestan nada."""
    import openai
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise LLMUnavailable("OPENROUTER_API_KEY no está definida en .env")
    client = openai.OpenAI(base_url=OPENROUTER_BASE_URL, api_key=key, timeout=180, max_retries=0)
    messages = [{"role": "user", "content": prompt + "\n\nRespondé SOLO con JSON válido según este schema:\n"
                 + json.dumps(schema, ensure_ascii=False)}]
    try:
        resp = client.chat.completions.create(
            model=model, messages=messages,
            response_format={"type": "json_schema",
                             "json_schema": {"name": schema_name, "schema": schema, "strict": True}})
    except openai.BadRequestError:
        # No todos los modelos free soportan json_schema: caer a json_object.
        resp = client.chat.completions.create(model=model, messages=messages,
                                              response_format={"type": "json_object"})
    text = resp.choices[0].message.content or ""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    usage = getattr(resp, "usage", None)
    return text, getattr(usage, "prompt_tokens", 0) or 0, getattr(usage, "completion_tokens", 0) or 0


def _call_gemini(model: str, prompt: str, schema: dict, file_path: Path | None) -> tuple[str, int, int]:
    from google import genai
    from google.genai import types
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise LLMUnavailable("GEMINI_API_KEY no está definida en .env")
    client = genai.Client(api_key=api_key)
    contents: list = []
    if file_path:
        mime = "application/pdf" if str(file_path).lower().endswith(".pdf") else "text/plain"
        contents.append(types.Part.from_bytes(data=Path(file_path).read_bytes(), mime_type=mime))
    contents.append(prompt)
    try:
        cfg = types.GenerateContentConfig(response_mime_type="application/json",
                                          response_json_schema=schema)
    except (TypeError, ValueError):   # SDK viejo sin response_json_schema
        cfg = types.GenerateContentConfig(response_mime_type="application/json")
    resp = client.models.generate_content(model=model, contents=contents, config=cfg)
    meta = getattr(resp, "usage_metadata", None)
    out_tokens = (getattr(meta, "candidates_token_count", 0) or 0) + (getattr(meta, "thoughts_token_count", 0) or 0)
    return resp.text, getattr(meta, "prompt_token_count", 0) or 0, out_tokens


def _dispatch(model: str, prompt: str, schema: dict, schema_name: str,
              path: Path | None, role: str, effort: str = "low") -> tuple[str, int, int]:
    if model.startswith("gemini"):
        return _call_gemini(model, prompt, schema, path)
    if path:
        raise LLMUnavailable(f"el rol {role} ({model}) no lee archivos; usar role='pdf'")
    if model.startswith("openrouter/"):
        return _call_openrouter(model.split("/", 1)[1], prompt, schema, schema_name)
    return _call_openai(model, prompt, schema, schema_name, effort)


def complete_json(prompt: str, schema: dict, *, role: str = "cheap", proposito: str = "",
                  file_path: str | Path | None = None, conn: sqlite3.Connection | None = None,
                  schema_name: str = "respuesta", use_cache: bool = True, max_usd: float | None = None,
                  reasoning_effort: str = "low") -> dict:
    """Llama al modelo del rol y devuelve el JSON parseado.

    Levanta LLMUnavailable (o BudgetExceeded / QuotaExhausted) cuando no puede
    responder; el caller decide el fallback (normalmente: dejar pendiente).
    """
    if os.getenv("LIC_LLM_OFFLINE") == "1":
        raise LLMUnavailable("LIC_LLM_OFFLINE=1")
    model = resolve_model(role)
    path = Path(file_path) if file_path else None
    key = _cache_key(model, prompt, schema, path)
    cache_file = config.cache_dir("llm") / f"{key}.json"
    if use_cache and cache_file.exists():
        _log_call(conn, model=model, proposito=proposito, cache_hit=True, prompt_hash=key[:16])
        return json.loads(cache_file.read_text(encoding="utf-8"))

    cap = float(max_usd if max_usd is not None else _llm_settings().get("max_usd_por_corrida", 2.0))
    if perfil() != "gratis" and run_spend() >= cap:
        raise BudgetExceeded(f"tope de USD {cap:.2f} por corrida alcanzado")

    pcfg = _perfil_cfg()
    max_attempts = int(pcfg.get("reintentos", 3))
    max_wait = float(pcfg.get("espera_maxima_s", 60))
    last_exc: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        _throttle(model)
        try:
            text, tin, tout = _dispatch(model, prompt, schema, schema_name, path, role)
            result = json.loads(text)
            cost = estimate_cost(model, tin, tout)
            with _SPEND_LOCK:
                _RUN_SPEND["usd"] += cost
            _log_call(conn, model=model, proposito=proposito, input_tokens=tin, output_tokens=tout,
                      costo_usd=cost, prompt_hash=key[:16])
            cache_file.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
            return result
        except LLMUnavailable:
            raise
        except json.JSONDecodeError as exc:
            last_exc = exc
            logger.warning("respuesta no-JSON de %s (intento %d)", model, attempt)
            continue
        except Exception as exc:  # errores del SDK: clasificar y decidir
            last_exc = exc
            kind = classify_error(exc)
            if kind in {"fatal", "quota"}:
                _log_call(conn, model=model, proposito=proposito, prompt_hash=key[:16],
                          error=f"{type(exc).__name__}: {str(exc)[:300]}")
                if kind == "quota":
                    raise QuotaExhausted(
                        f"{model}: cuota agotada ({str(exc)[:160]}). Lo pendiente queda para la "
                        "próxima corrida (en el plan gratis, la cuota diaria se renueva cada día).") from exc
                raise LLMUnavailable(f"{model}: error no recuperable: {exc}") from exc
            wait = min(max_wait, 5 * (2 ** attempt))
            logger.warning("%s: %s; reintento %d/%d en %.0fs", model, type(exc).__name__,
                           attempt, max_attempts, wait)
            time.sleep(wait)
    _log_call(conn, model=model, proposito=proposito, prompt_hash=key[:16],
              error=f"{type(last_exc).__name__}: {str(last_exc)[:300]}")
    raise LLMUnavailable(f"{model}: sin respuesta válida tras {max_attempts} intentos: {last_exc}")
