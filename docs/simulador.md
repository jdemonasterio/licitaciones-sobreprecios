# Simulador end-to-end

Corre la app como la usaria una persona real — filtrar licitaciones, marcar
una revision, armar un reporte, investigar un caso puntual, recalcular
precios — en un navegador real (Chromium, invisible), y avisa si algo se
rompio. Es el "gate" antes de entregar una version nueva a los usuarios,
igual que el simulador post-deploy de otros proyectos de la casa.

Los ~175 tests unitarios (`.venv/bin/python -m pytest -q`) prueban funciones
y rutas sueltas; esta suite prueba el JOURNEY completo, con la pagina
renderizada, el JS de verdad corriendo y los graficos dibujandose.

## Como correrlo

```bash
# Mac/Linux
.venv/bin/python scripts/simular.py

# Windows
simular.bat
```

Sale con codigo 0 si todo paso, y != 0 si algun escenario fallo (asi se
puede enganchar a un paso previo a entregar una version). Con `-k <texto>`
corre solo los escenarios cuyo nombre matchee (ej. `simular.py -k revision`).
Con `-v` muestra la salida completa de pytest en vez de solo la tabla.

Internamente es `LIC_E2E=1 .venv/bin/python -m pytest tests/e2e -v`. Sin
`LIC_E2E=1` esa carpeta se skippea entera (tests/e2e/conftest.py lo hace
explicito), asi que un `pytest` normal del repo no se entera de que existe.

## Que verifica (12 escenarios / 19 tests, `tests/e2e/test_user_journeys.py`)

1. **Inicio**: KPIs, filtros (producto/semaforo/busqueda) y orden.
2. **Detalle de licitacion**: evidencia y "como se calculo" se despliegan.
3. **Marcar revision**: NO recarga la pagina (soft-nav), el scroll y la nota
   sobreviven, aparece un cartel, y lo guardado persiste a un reload real.
4. **Reportes**: job en background -> cartel -> aparece en la lista -> el
   HTML tiene metodologia y limitaciones; Excel y CSV se descargan.
5. **Investigar**: pegar el texto de una resolucion (offline, regex) muestra
   los 5 proveedores; una entrada sin sentido da un mensaje amigable, nunca
   un stacktrace ni un 500; subir algo que no es PDF se rechaza.
6. **Datos y fuentes**: "Recalcular sobreprecios" (offline) corre en
   background, avisa con un cartel y conserva lo que el usuario toco.
7. **Empresas / Analitica / Producto**: listas, detalle y graficos (Chart.js)
   renderizan sin errores de consola.
8. **Seguridad**: un POST de otro origen (`Origin: http://evil.example`) se
   rechaza con 403.
9. **Layout**: a 390px de ancho (celular) no hay scroll horizontal; en modo
   oscuro el texto no queda del mismo color que el fondo.
10. **Doble submit**: dos POST concurrentes al mismo job solo arrancan UNA
    corrida (el guard de `web/_background.py` no se saltea).
11. **Revision humana** (MEJORAS #34, `sobreprecios/revision_humana.py`): la
    cola de `/revision` lista el renglon gris y el rojo de la semilla y
    filtra por origen; en `/revision/<id>` corregir el producto asignado de
    un caso gris recalcula EN EL LUGAR (sin recargar) y el motivo cambia;
    marcar la observacion que forma el minimo confirmado como "no es el
    mismo producto" saca ese renglon del rojo (sin tocar los demas
    renglones del mismo producto); `/doc` con un host no permitido devuelve
    un error en castellano, nunca sigue el link.
12. **Validacion in-line en `/licitacion/<id>`**: marcar una parte de un
    renglon (extraccion/producto/comparacion) con "OK" no recarga la pagina
    y el resumen ("validado por…" / "con objeciones") persiste despues de
    navegar a otra pagina y volver.

Todo escenario corre **sin red** (las fuentes externas de precios quedan
detras de un proxy que no escucha en nadie: fallan rapido y la app sigue
"degradada", como lo hace en produccion si el plan gratis de IA se queda sin
cuota) y **sin tocar `data/sobreprecios.db`**: la base y el cache son
temporales, sembrados offline con el propio codigo del repo
(`tests/e2e/conftest.py`, funcion `_seed`) a partir de
`tests/fixtures/bo_mds_res735_2021.txt` (una resolucion real del Boletin
Oficial con 5 proveedores de leche en polvo) mas unos precios de mercado
sinteticos pensados para que salga un renglon de cada semaforo (rojo,
amarillo, verde) y un producto sin precios de mercado (gris).

## Si algo falla

- La tabla final de `simular.py` dice que escenario fallo.
- Cada falla guarda una captura de pantalla en
  `_scratch/licitaciones/simulador/<fecha-hora>/`.
- La salida completa de pytest (con el traceback) se imprime abajo de la
  tabla cuando hay fallas.

## Dependencia nueva (propuesta, no aplicada)

`playwright` ya esta instalado en `.venv` (con Chromium) pero no esta
declarado en `requirements.txt` — se dejo asi a proposito en esta tanda para
no tocar ese archivo sin que lo decidas vos. Si se adopta el simulador,
conviene agregarlo como dependencia de DESARROLLO (no la necesita nadie en
produccion, solo quien corre `simular.py`/`simular.bat`):

```
# requirements-dev.txt (archivo nuevo) o una seccion aparte en requirements.txt
playwright>=1.47
```

y despues `.venv/bin/python -m playwright install chromium` (ya corrido en
esta maquina).

## Como agregar un escenario

1. En `tests/e2e/test_user_journeys.py`, agregar una funcion
   `test_<algo_en_castellano_bien_descriptivo>(page, seeded_app)`.
2. Usar `page.goto("/la-ruta")`, interactuar como lo haria una persona
   (`page.fill`, `page.click`, `page.select_option`...) y asertar sobre lo
   que se VE o sobre la base (`conftest.read_db(...)`), nunca sobre texto
   interno que el usuario no lee.
3. Terminar con `conftest.assert_clean(page)` (sin errores de consola, sin
   5xx) salvo que el escenario sea justamente de seguridad/errores.
4. Si el escenario necesita datos nuevos, agregarlos en `_seed()`
   (`tests/e2e/conftest.py`) — nunca en `data/sobreprecios.db`.
5. Si el escenario crea algo que vive fuera de la base temporal (como un
   reporte: ver la nota de `reports.reportes_dir()` mas abajo), limpiarlo en
   un `try/finally`, como hace `test_reportes_...`.

## Limitaciones conocidas (no son bugs de este simulador, son del alcance)

- No se sembro un proceso de COMPR.AR (`sources/comprar.py` +
  `pipeline/comprar_import.store_detalle`): es opcional segun el pedido
  original y los 2 procesos del Boletin Oficial ya alcanzan para cubrir los
  10 escenarios. Si se necesita, `tests/test_comprar_import.py` tiene el
  patron con los fixtures de `tests/fixtures/comprar/`.
- El escenario de "Investigar" con texto pegado usa `extract_document(...,
  use_llm=True)` por dentro (asi lo llama `investigar.py`): sigue siendo
  100% offline porque el fixture elegido se resuelve por regex antes de
  intentar el LLM, y `LIC_LLM_OFFLINE=1` corta cualquier intento de todas
  formas.
- Un hallazgo de la app (no corregido aca, ver el reporte de la sesion que
  armo este simulador): `reports.reportes_dir()` e
  `investigar.guardar_upload()` usan `config.resolve()`, que SIEMPRE apunta
  a `<repo>/data/...`, sin pasar por `LIC_DB_PATH`/`LIC_CACHE_DIR`. El
  escenario de Reportes limpia los 3 archivos que crea para no dejar
  basura ahi.
