# Mejoras pendientes

Esta es la lista de cosas que faltan o se pueden mejorar en la herramienta. Esta
pensada para dos lectores: vos (para elegir que pedir primero) y Claude Code
(para implementarla).

**Como usar este archivo**: abri Claude Code en la carpeta del proyecto (ver
[guia_inicio.html](docs/guia_inicio.html), etapa 11) y decile algo como "hace la
mejora #7" o "lee MEJORAS.md y hace la mejora #12". Claude va a leer el detalle
de ese numero, hacer el cambio, correr los tests y avisarte que modifico.

Las que dicen **"pendiente de check de Mateo"** son decisiones que el LLM prime
ya analizo (ver `_scratch/licitaciones/DECISIONES_prime.md`, no se sube a git)
pero que Mateo todavia no marco como aprobadas. Pedile a Claude que implemente
esas solo despues de marcarlas `[x] OK` en ese archivo.

---

## Datos y fuentes

**#1 — Bajar el acto de adjudicacion final, no solo el dictamen.**
Hoy "quien gano" sale del Dictamen de Evaluacion (que *recomienda*, no decide).
Falta bajar el acto administrativo final y sus anexos para confirmar ganador,
cantidad y precio por renglon. *Pendiente de check de Mateo (P6).*
Como verificar: un caso de COMPR.AR muestra un renglon con fuente "acto de
adjudicacion" en vez de (o ademas de) "recomendado (dictamen)".

**#2 — Leer la condicion de pago real del pliego.**
El costo financiero hoy usa 1 mes fijo como aproximacion por IPC. Falta leer
plazo, hito desde el que corre, y anticipos del pliego/contrato de cada caso.
*Pendiente de check de Mateo (P2).*
Como verificar: el detalle de una licitacion muestra el plazo de pago leido del
pliego (no solo "1 mes, aproximado").

**#3 — Completar COMPR.AR: filtro de fechas del servidor + PDFs de pliegos/anexos.**
El filtro de fechas de busqueda se aplica hoy del lado nuestro (lento con
rangos grandes); los PDFs de pliegos y anexos no se descargan directo.
Como verificar: `python -m sobreprecios.cli comprar --desde X --hasta Y` tarda
lo mismo sin importar el tamano del rango, y un proceso trae su pliego adjunto.

**#4 — Backfill de precios historicos con Wayback Machine para casos rojos.**
Para compras viejas (2020-2023) sin precio de mercado propio, probar snapshots
de mayoristas en archive.org como evidencia puntual (no automatica).
Como verificar: un caso de "Investigar" de 2021 muestra una referencia con
fuente "Wayback Machine" cuando no hay otra.

**#5 — Pedido formal a Secretaria de Comercio del historico SEPA 2023-2026.**
No existe un archivo publico de precios por producto de esos anos; pedirlo
formalmente vale mas que deflactar precios de hoy durante anos.
*Pendiente de decision (P8).* Accion del responsable del proyecto, no una tarea de codigo:
redactar el pedido de acceso a informacion publica.
Como verificar: existe un borrador de nota/pedido en el repo o en Drive.

**#6 — Sumar mayoristas regionales (empezar por Diarco).**
Hoy el unico mayorista en vivo es Maxiconsumo (sucursal Moreno): para compras
en otras provincias esa referencia puede no ser representativa.
*Pendiente de check de Mateo (P9).*
Como verificar: "Datos y fuentes" lista una segunda fuente mayorista con fecha
de observacion y, si falla, dice "sin datos" (nunca reusa un precio viejo).

**#7 — Parser de PDF superior para actas y anexos escaneados.**
Evaluar pdfplumber + OCR local + Gemini como respaldo controlado (sin
PyMuPDF, que es AGPL) para mejorar la lectura de PDFs viejos o escaneados.
*Esperando el benchmark en curso (P10).*
Como verificar: existe `sobreprecios/pdf_text.py` (o similar) con la cadena
recomendada, y los tests de extraccion de PDF pasan.

**#8 — Arreglar rutas que ignoran `LIC_DB_PATH`/`LIC_CACHE_DIR`.**
`reports.reportes_dir()` e `investigar.guardar_upload()` usan `config.resolve()`,
que siempre apunta a `<repo>/data/...` sin pasar por esas variables de entorno
(encontrado armando el simulador, ver [simulador.md](docs/simulador.md)).
Como verificar: corriendo el simulador (`LIC_DB_PATH`/`LIC_CACHE_DIR` apuntando
a una carpeta temporal) los reportes y los uploads de "Investigar" NO aparecen
en `data/` del repo real.

**#9 — Declarar `playwright` como dependencia de desarrollo.**
Ya esta instalado en `.venv` (lo usa `scripts/simular.py`) pero no figura en
ningun `requirements*.txt` todavia, a proposito, hasta decidir si se adopta.
Como verificar: existe `requirements-dev.txt` (o una seccion aparte) con
`playwright>=1.47` y `simular.bat` sigue funcionando en una maquina nueva.

## Calidad y validacion

**#10 — Fijar el modelo del juez LLM externo.**
El modulo de auditoria (`auditoria/`, solo lectura, tope de gasto por corrida)
ya esta implementado, pero el modelo por defecto queda sin fijar hasta medir
su acuerdo contra los veredictos de Mateo en una muestra real.
*Pendiente de check de Mateo (P11).*
Como verificar: `_scratch/licitaciones/auditoria/<fecha>/` tiene un resumen con
tasa de acuerdo juez-humano y el modelo elegido.

**#11 — Golden de validacion de uniones de entidades.**
Falta generar `validation_entidades_*.tsv` (cada alias → clave canonica de
empresa/organismo/persona) para que Mateo marque cuales uniones son correctas.
Como verificar: `python -m sobreprecios.cli goldens` genera ese TSV en
`_scratch/licitaciones/` con columnas `Tu veredicto` / `Tu comentario`.

**#12 — Distinguir fecha del acto, vigencia y publicacion en autoridades.**
Una designacion de directorio puede ser parcial: hoy el riesgo es cerrar todo
el directorio anterior aunque el acto nuevo no lo reemplace entero.
Como verificar: un hecho societario de `empresa_hechos` muestra las 3 fechas
por separado, y una designacion parcial no cierra cargos que no menciona.

**#13 — Revisar y marcar las 23 decisiones del LLM prime.**
`_scratch/licitaciones/DECISIONES_prime.md` (no se sube a git) tiene 23
decisiones ya tomadas por el LLM prime (P1-P15 y la ronda de método P16-P23), esperando que Mateo marque cada una
`[x] OK` o `[ ] cambiar: ...`. Varias mejoras de esta lista dependen de eso.
Como verificar: ese archivo no tiene ninguna fila `Tu check` vacia.

**#26 — Atributos coexistentes por campo (no una sola "variante").**
Hoy cada producto tiene una sola dimensión de variante. Leche: fortificación y
formulación conviven; caballa: cobertura y "sin sal"; jabón: infantil +
glicerina. El prime (P17/P22) pidió atributos separados con estado propio y
parseo clave-valor de SIByS. Como verificar: "Caballa en aceite y agua sin sal"
da cobertura=aceite_y_agua y sal=sin, no "ambiguo".

**#27 — Clave de serie estable (EAN + sucursal) y IDs de la evidencia.**
La serie hoy es fuente + descripción normalizada (provisional); el par que
confirma el mínimo se guarda solo como las 2 primeras filas de la evidencia.
Como verificar: cada comparación guarda los ids de `precios_mercado` que la
forman y la corroboración usa EAN cuando existe.

**#28 — Cuarentena de precios sospechosos en vez de borrarlos por mediana.**
Prime (riesgos ronda 2): el filtro de outliers descarta datos sin probar el
error. Como verificar: una observación fuera de rango queda visible con
"cuarentena: <razón>" y se puede rehabilitar.

**#29 — Estado documental en todo el sistema (dictamen / contrato / pago).**
Los textos todavía dicen "el Estado pagó" para recomendaciones de dictamen.
Como verificar: cada renglón y reporte dice "recomendado", "contratado" o
"pagado" según la fuente.

**#30 — Índices con fecha de publicación y versión.**
Prime P20: guardar cuándo se publicó cada IPC y permitir el modo "lo que se
sabía a la fecha". Como verificar: tabla `indices` con `fecha_publicacion` y
`version`; el comparador puede elegir modo.

**#31 — Exportación externa que rechace lo no publicable.**
Hoy los reportes llevan banner BORRADOR INTERNO pero se pueden mandar igual.
Como verificar: el botón "Exportar para prensa" falla si hay un renglón sin
"publicable", y la huella cubre evidencia, catálogo y configuración.

**#32 — Requisitos de envase y certificación (libre de gluten, PET, papel).**
El juez marcó compras con "libre de gluten" o envase de papel comparadas con
productos que no lo acreditan. Decidir qué requisitos son críticos para el
precio. Como verificar: lista en `config/productos_ajustes.yml` de requisitos
críticos por producto y test que lo cubra.

**#33 — Volcados SEPA "as-of" para compras sin volcado previo.**
El archivo comunitario no tiene volcados entre 2026-03-16 y 2026-07-01: las
compras de abril-junio 2026 no pueden ser rojas. Pedir el histórico oficial
(P8) o sumar otra fuente diaria. Como verificar: `sepa_archivo` encuentra un
volcado <= fecha a <= 15 días para cada compra comparable.

## Pantallas (webui)

**#14 — Tabla de correspondencias de organismos renombrados.**
Ya esta implementado que el sistema NO une renombres automaticamente (ej.
Desarrollo Social → Capital Humano), pero falta la vista de agrupacion
historica con las correspondencias documentadas, para quien la quiera ver.
*Pendiente de check de Mateo (P13).*
Como verificar: "Analitica" permite ver un ranking "agrupado historicamente"
que explica que organismos agrupa y con que fuente.

**#15 — Mostrar cobertura de fuentes en los rankings.**
El minimo de mercado tiende a bajar cuanto mas fuentes se consultan: dos
organismos con distinta cobertura pueden quedar mal comparados entre si por
el metodo de muestreo, no por su comportamiento real.
Como verificar: "Analitica" muestra, al lado de cada ranking, cuantas fuentes
de precio tenia disponibles cada caso.

## Empresas

**#16 — Cruce de mismo domicilio / mismas personas entre oferentes.**
Util para detectar senales de posible cartelizacion dentro de un mismo
renglon (varios oferentes con el mismo domicilio o las mismas personas).
Como verificar: la pagina de un proceso con mas de un oferente muestra una
alerta si dos o mas comparten domicilio o una persona en comun (con fuente).

**#17 — Sumar sanciones (ONC / SIPRO / BCRA deudores) como contexto.**
Contexto adicional sobre una empresa adjudicataria, nunca como prueba de
irregularidad en la compra en si.
Como verificar: la pagina de una empresa muestra una seccion "Antecedentes"
con sanciones encontradas y su fuente, o "sin antecedentes encontrados".

**#18 — Cruce con declaraciones juradas de funcionarios (OA), solo por CUIT.**
Nunca por apellido (ya hubo homonimos reales encontrados en esta base).
Como verificar: un vinculo funcionario-empresa en el webui siempre muestra el
CUIT/DNI que lo confirma; sin ese dato, no se muestra el vinculo.

## Catalogo

**#19 — Ampliar el catalogo de limpieza e higiene.**
Orden sugerido: limpiadores/detergentes (formulacion y dilucion) → bolsas
(medida, espesor, material) → papel higienico/toallas (largo, ancho, capas) →
aderezos/especias (composicion y presentacion equivalente). Lavandina necesita
concentracion de cloro verificada; guantes quedan para una fase aparte.
*Pendiente de check de Mateo (P7, incluye decidir que especias puntuales).*
Como verificar: `config/productos.yml` tiene los productos nuevos con sus
atributos criticos obligatorios, y sus tests pasan.

**#20 — Confirmar las presentaciones INDEC de yerba y leche en polvo.**
Hoy estan marcadas `verificado: inferido` (500 g y 800 g respectivamente): no
pueden ser referencia principal hasta confirmarse con la fuente oficial.
Como verificar: `config/productos.yml` muestra `verificado: confirmado` para
esos dos productos, con la fuente que lo confirma.

## Operacion

**#21 — Programar `actualizar_datos.bat` en el Programador de tareas de Windows.**
Cada dia que no se corre es un dia de precios de mercado que se pierde para
siempre (no existe un archivo historico publico). Ver
[guia_inicio.html](docs/guia_inicio.html), etapa 7, para el paso a paso.
Como verificar: el Programador de tareas de Windows tiene una tarea diaria que
corre `actualizar_datos.bat` sin intervencion manual.

**#24 — Ampliar a mas jurisdicciones (provincias/municipios).**
PBAC, BAC CABA (con datos OCDS), boletines provinciales — hoy solo cubre
compras nacionales.
Como verificar: `python -m sobreprecios.cli estado` muestra procesos de al
menos una jurisdiccion provincial o municipal ademas de la nacional.

---

## Limitaciones conocidas (no son bugs, son el alcance de hoy)

- **COMPR.AR**: solo se puede leer lo que el sitio muestra sin login; el
  filtro de fechas de la busqueda se aplica de nuestro lado, no del sitio.
- **Mayorista en vivo**: solo Maxiconsumo (sucursal Moreno, sin login).
  Supermercados: AMBA + el espejo SEPA por provincia.
- **Boletin Oficial 2da seccion** (datos de empresas) solo cubre sociedades
  con sede en CABA; los avisos anteriores a ~2012 son escaneos de baja
  confianza.
- **No existe un archivo publico de precios 2023-2026 por producto**: se
  acumula una foto diaria desde que se empezo a correr `precios-hoy` (por eso
  la mejora #21 importa tanto).
- `datos.gob.ar` y `datos.produccion.gob.ar` no respondieron desde la red de
  desarrollo (probable geobloqueo); el espejo SEPA en GitHub cubre el minorista
  mientras tanto.
- **Ningun caso sale a periodistas sin revision humana** y estado
  "publicable": esto es una regla de diseno a proposito (ver
  [CLAUDE.md](CLAUDE.md)), no algo pendiente de arreglar.
- **Una brecha de precio no prueba una irregularidad.** Logistica, plazos de
  pago, especificaciones tecnicas o errores de publicacion pueden explicarla;
  por eso cada caso se revisa a mano antes de compartirlo.
