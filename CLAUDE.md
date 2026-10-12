# CLAUDE.md

Guía para Claude Code (o cualquier asistente) que trabaje en este repo.

## Qué es

Herramienta local (Python 3.12 + SQLite + FastAPI) para detectar posibles sobreprecios en compras del Estado
argentino de bienes de góndola. La usan personas **no técnicas** de un organismo público, en Windows. Leé
[README.md](README.md) y [DESIGN_sobreprecios.md](DESIGN_sobreprecios.md) antes de cambiar algo.

## Reglas

- **Un resultado falso es peor que ninguno.** Esto termina en manos de periodistas. Ante la duda, el sistema marca
  "revisar" o "sin comparación", nunca "sobreprecio". No relajes los guardas de `pipeline/compare.py`
  (plausibilidad, confianza, outliers) sin una razón escrita en DESIGN_sobreprecios.md.
- **Todo dato externo es no confiable** (avisos del BO, páginas de COMPR.AR, precios de supermercados, respuestas de
  LLM). Nunca controla el flujo sin validación; nunca se ejecuta.
- **Variables de entorno**: solo las de `.env.example`. No agregues claves de otros servicios.
- **Configuración en YAML** (`config/`), no en el código: criterios de comparación, catálogo de productos, perfiles
  de LLM.
- **LLM barato y con cache**: usá `sobreprecios.llm.complete_json` con un schema estricto; nunca llames a un SDK
  directo. El perfil gratis es el default.
- **Webui**: una acción nunca recarga la página ni borra lo que el usuario escribió. Trabajo largo → job en
  `web/_background.py`; formularios POST pasan por `static/soft-nav.js`; scripts inline idempotentes.
- **Tests**: toda lógica nueva no trivial con su test en `tests/` (pytest, sin red: fixtures en `tests/fixtures/`).
  `python -m pytest -q` tiene que pasar antes de terminar.
- **Validación manual**: para tablas que una persona tiene que revisar, generá TSV en `_scratch/licitaciones/`
  (ver `sobreprecios/goldens.py`): ids estables en columnas 4-5, al final lo que se lee para decidir y las columnas
  vacías "Tu veredicto" / "Tu comentario".
- **Textos para usuarios** en castellano rioplatense, claros, sin jerga. Lenguaje prudente en todo lo que pueda
  publicarse: "posible sobreprecio", "pagó X % más que la referencia"; nunca acusaciones.
- `_scratch/` y `data/` no se commitean.
