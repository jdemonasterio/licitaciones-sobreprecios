# Despliegue en la nube: pagina publica con dominio

Esta guia es para cuando quieras que la herramienta deje de ser "algo que corro en mi
compu" y pase a tener una direccion de internet fija, abierta desde cualquier celular,
para mostrarsela a periodistas o al publico.

No hace falta saber programar, pero si usar una tarjeta (la plataforma elegida cobra
por uso, aunque el plan de entrada es barato) y tener un poco de paciencia la primera vez
(30-40 minutos).

**Antes de nada, lee la seccion "Advertencias de seguridad" mas abajo.** Una pagina
publica sin modo de solo lectura o sin clave puede terminar con cualquiera borrando datos
o gastando la plata de las claves de IA.

## Que plataforma usamos: Railway

Hay varias formas de poner esto en internet. La mayoria de las "gratis" (Vercel,
Netlify) **no sirven para este proyecto**: estan pensadas para paginas que no guardan
nada en disco, y esta herramienta necesita un archivo (la base SQLite) que tiene que
sobrevivir entre reinicios, ademas de poder correr trabajos largos en segundo plano
(bajar el Boletin Oficial, llamar al LLM). Para eso hace falta un servicio "siempre
prendido" con un disco persistente.

Elegimos **Railway** por esto:

1. Es el mas simple de los que SI soportan disco persistente: conecta directo con
   GitHub, un click para desplegar, un click para agregar el disco.
2. El plan de entrada ("Hobby") sale **USD 5 por mes** e incluye esos USD 5 de uso: para
   una pagina con poco trafico como esta, en la practica terminas pagando ese minimo.
   El disco cuesta aparte, USD 0,15 por GB por mes (la base de este proyecto pesa pocos
   MB al principio): el total realista es **entre USD 5 y 7 por mes**.
3. No hay que tocar una consola ni instalar nada en tu compu: todo se hace desde el
   navegador, con botones.
4. Soporta `Dockerfile` (lo que agregamos en este repo) sin configuracion extra.

**Alternativa: Render.** Tambien sirve y tambien es simple de usar, pero el disco
persistente solo esta disponible en un plan pago desde USD 7 por mes (el plan free NO
permite disco), asi que termina costando un poco mas que Railway para lo mismo. Dejamos
un archivo `render.yaml` ya preparado por si en algun momento quieren mudarse ahi: los
pasos de abajo son casi los mismos, cambiando "Railway" por "Render" y usando el boton
"New > Blueprint" en lugar de "New Project".

(Descartamos Fly.io por necesitar su propia consola de comandos (`flyctl`) para cosas
que en Railway son un click, y los free tiers de Oracle Cloud / Google por pedir tarjeta
y configuracion de servidor mucho mas tecnica — no son "apto no-tecnico".)

## Antes de empezar: que necesitas

- Un email y una tarjeta de credito o debito (Railway no cobra nada si te quedas dentro
  del plan Hobby, pero pide la tarjeta para habilitarlo).
- Una cuenta de GitHub. El codigo ya esta publicado en
  `https://github.com/jdemonasterio/licitaciones-sobreprecios` — no hace falta subir
  nada, solo "conectar" esa cuenta con Railway.
- Las claves de IA que ya usas en tu compu (ver [guia_claves_api.md](guia_claves_api.md)):
  `GEMINI_API_KEY` y, si la tenes, `OPENAI_API_KEY`. Las vas a copiar a Railway, nunca al
  codigo.
- Decidido de antemano: **la pagina va a ser de solo lectura, o va a tener clave, o las
  dos cosas.** No hay una tercera opcion segura.

## Paso 1 — Crear la cuenta en Railway

1. Entra a `https://railway.com` y hace click en "Login" / "Start a New Project".
2. Elegi "Continuar con GitHub" (es lo mas simple: usa la misma cuenta del paso
   anterior).
3. Autoriza a Railway a ver tus repos de GitHub cuando te lo pida.
4. Cuando te pida un metodo de pago para activar el plan Hobby, cargalo (no te cobra
   nada todavia, es para habilitar el plan de USD 5).

## Paso 2 — Crear el proyecto y conectar el repo

1. Desde el dashboard, "New Project" → "Deploy from GitHub repo".
2. Buscá y elegi `jdemonasterio/licitaciones-sobreprecios`.
3. Railway va a detectar el `Dockerfile` de este repo solo (no hace falta elegir nada
   mas) y va a arrancar un primer build. **Este primer intento probablemente falle o
   quede andando mal** — todavia falta el disco y las variables de los pasos
   siguientes. Es normal, seguí.

## Paso 3 — Agregar el disco (donde vive la base de datos)

Sin esto, cada vez que Railway reinicia el contenedor se pierde TODO lo bajado.

1. Entra al servicio que se creo (el nombre del repo) → pestaña **"Volumes"**.
2. "New Volume" (o el boton que diga "Add Volume").
3. Punto de montaje (**"Mount path"**): escribi `/data`.
4. Tamaño: 1 GB alcanza de sobra para empezar (se puede agrandar despues sin perder
   datos).
5. Guardar.

El `Dockerfile` de este repo ya deja la base y los caches apuntando a `/data` por
default (`LIC_DB_PATH`, `LIC_CACHE_DIR`), asi que con montar el volumen ahi ya alcanza.

## Paso 4 — Variables de entorno

Pestaña **"Variables"** del servicio. Agrega estas (una por una, "New Variable"):

| Variable | Valor | Para que |
|---|---|---|
| `GEMINI_API_KEY` | tu clave | el LLM que lee PDFs y textos del BO |
| `OPENAI_API_KEY` | tu clave (si la tenes) | LLM de pago, opcional |
| `LIC_LLM_PERFIL` | `gratis` | mismo criterio que en tu compu |
| `LIC_MODO_PUBLICO` | `1` | **recomendado si vas a dejarla abierta a cualquiera**: nadie puede guardar cambios, correr busquedas nuevas ni usar "Investigar" desde ahi |
| `LIC_USUARIO` | el que elijas | si ademas (o en vez de lo anterior) queres pedir contraseña a TODA la pagina |
| `LIC_CLAVE` | una clave larga, no obvia | va con `LIC_USUARIO` — si dejas una de las dos vacia, no se pide clave |
| `LIC_HOSTS_PERMITIDOS` | el dominio que te dio Railway (ver Paso 5) | sin esto, Railway puede no dejarte entrar por seguridad (ver mas abajo) |

No hace falta tocar `LIC_DB_PATH` ni `LIC_CACHE_DIR`: ya vienen bien por default apuntando
al volumen `/data` del Paso 3.

**Nunca pongas estas claves en el codigo ni en GitHub.** Van SOLO en esta pestaña de
Railway.

## Paso 5 — Deploy y primer arranque

1. Con el disco y las variables ya puestas, Railway reinicia el servicio solo (o apretá
   "Redeploy" si no lo hace).
2. En la pestaña **"Deployments"**, abri el ultimo y mira los "Logs": el primer arranque
   tarda un poco mas de lo normal porque carga el catalogo de productos y los precios de
   INDEC (ves lineas como `catalogo: 34 productos` y `INDEC: ... valores de IPC`). Las
   proximas veces que reinicie va a ser instantaneo, porque la base ya existe en el
   disco.
3. Cuando el log diga `Uvicorn running on http://0.0.0.0:8000`, esta arriba.
4. Railway te da una direccion del tipo `https://licitaciones-sobreprecios-production.up.railway.app`
   (pestaña **"Settings" → "Networking" → "Generate Domain"** si no aparece sola). Copiala
   y pegala como valor de `LIC_HOSTS_PERMITIDOS` en el Paso 4 si todavia no lo hiciste, y
   volve a desplegar.
5. Abri esa direccion desde el celular: tiene que verse la pagina de siempre, con el
   cartel "Version publica de solo lectura" arriba si activaste `LIC_MODO_PUBLICO`.

**Importante — el primer arranque llena solo el catalogo y los precios de referencia
(INDEC, cotizaciones), pero NO sale a buscar licitaciones del Boletin Oficial ni de
COMPR.AR solo**: eso se hace a mano, una vez, desde "Datos y fuentes" (si la pagina no
esta en modo publico) o localmente y despues subiendo la base — ver "Problemas
comunes" mas abajo.

## Paso 6 — Dominio propio (opcional)

Si en vez de `...up.railway.app` queres algo como `sobrepreciosestado.com.ar`:

1. Compralo en cualquier lugar (NIC Argentina para `.com.ar`, o Namecheap/GoDaddy para
   `.com`). Cuesta entre USD 10 y 20 por año.
2. En Railway: **"Settings" → "Networking" → "Custom Domain"**, escribi tu dominio.
3. Railway te muestra un registro **CNAME** para copiar.
4. Entra al panel de donde compraste el dominio ("DNS" o "Nameservers"), agrega ese
   CNAME con los valores que te dio Railway.
5. Tarda entre 10 minutos y unas horas en propagarse. Cuando funcione, agrega ese
   dominio tambien a `LIC_HOSTS_PERMITIDOS` (Paso 4) y volve a desplegar.

## Como actualizar cuando cambia el codigo

Railway queda conectado al repo de GitHub: cada vez que se hace `git push` a la rama
`main`, Railway **redespliega solo**, en un par de minutos, sin perder la base (vive en
el disco, no en el codigo). No hay que hacer nada manual.

## Como hacer un backup del volumen

La base (`/data/sobreprecios.db`) es lo unico irremplazable (todo lo demas se puede
volver a bajar). Para bajarla a tu compu:

1. Instala la [CLI de Railway](https://docs.railway.com/cli) (`npm i -g @railway/cli` o
   el instalador de la pagina).
2. `railway login`, despues `railway link` parado en la carpeta del proyecto en tu
   compu, y elegi este servicio.
3. `railway volume files download /sobreprecios.db ./backup_sobreprecios.db` — baja el
   archivo del volumen directo por SFTP, sin tener que entrar al contenedor.
   (Alternativa con interfaz: `railway volume browse /` abre un explorador de archivos
   en la terminal para navegar, bajar o subir archivos del volumen a mano.)

Hacelo antes de cualquier cambio grande (por ejemplo, antes de cambiar `config/productos.yml`
en produccion). Para restaurar un backup, es el mismo comando al reves:
`railway volume files upload ./backup_sobreprecios.db /sobreprecios.db`.

## Costos — resumen

| Item | Costo aproximado |
|---|---|
| Plan Railway Hobby | USD 5/mes (incluye esos USD 5 de uso) |
| Disco persistente (1 GB) | ~USD 0,15/mes |
| Dominio propio (opcional) | USD 10-20/**año** |
| Claves de IA (Gemini plan gratis) | USD 0 si te quedas en el perfil `gratis` |
| **Total tipico** | **USD 5 a 7 por mes** (+ el dominio si lo compras) |

## Advertencias de seguridad (leer antes de compartir el link)

- **Nunca dejes la pagina publica sin `LIC_MODO_PUBLICO=1` o sin `LIC_USUARIO`/`LIC_CLAVE`.**
  Sin ninguna de las dos, cualquiera que tenga el link puede corregir datos, marcar
  licitaciones, borrar correcciones o gastar la cuota de las claves de IA usando
  "Investigar".
- Las dos se pueden combinar: por ejemplo, clave para el equipo mientras se prueba, y
  despues sacar la clave y dejar solo `LIC_MODO_PUBLICO=1` para el publico en general.
- Las claves de API (`GEMINI_API_KEY`, `OPENAI_API_KEY`) van UNICAMENTE en la pestaña
  "Variables" de Railway. Nunca en el codigo, nunca en un mensaje de Slack o email,
  nunca visibles en una captura de pantalla que se comparta.
- `LIC_USUARIO` / `LIC_CLAVE` conviene compartirlos por un canal privado (no por el
  mismo link publico que vas a difundir).
- Si en algun momento sospechas que la clave se filtro, cambiala en la pestaña
  "Variables" (redespliega solo) — es gratis e inmediato.

## Problemas comunes

- **"Application failed to respond" / la pagina no carga al principio**: mira los Logs
  del deployment — casi siempre es que todavia falta el volumen (Paso 3) o alguna
  variable (Paso 4).
- **El primer arranque fallo a mitad de camino (ej. sin INDEC por un corte de internet) y
  la pagina quedo a medias**: el contenedor sigue levantando el webui igual, pero como la
  base ya existe en el disco, un simple "Restart" NO reintenta el `setup`. Si la pagina
  NO esta en modo publico, lo mas facil es entrar a "Datos y fuentes" y apretar el boton
  que corresponda (por ejemplo "Actualizar IPC, precios INDEC y cotizaciones"). Si esta en
  modo publico (sin esos botones a la vista), conectate con `railway ssh` (te da una
  terminal DENTRO del contenedor) y corre `python -m sobreprecios.cli indec` a mano.
- **Quiero mostrar datos reales, no una base vacia**: lo mas simple es correr el pipeline
  completo en tu compu primero (`pipeline --desde ... --hasta ...`), confirmar en
  `/revision` que los casos estan bien, y despues subir esa base ya armada al volumen
  con `railway volume files upload ./sobreprecios.db /sobreprecios.db` (mismo comando que
  el de "restaurar un backup" mas arriba) en vez de dejar que la nube arranque de cero.
- **Olvide poner `LIC_HOSTS_PERMITIDOS` y la pagina rechaza las visitas**: es la misma
  proteccion que ya corre en tu compu contra ataques de "DNS rebinding" (ver
  `sobreprecios/web/app.py`). Agrega el dominio que te dio Railway a esa variable y
  volve a desplegar.
- **Quiero bajar el costo**: con `LIC_LLM_PERFIL=gratis` las llamadas de IA no cuestan
  nada aparte de lo que ya pagas en Railway; el disco de 1 GB alcanza por meses.
