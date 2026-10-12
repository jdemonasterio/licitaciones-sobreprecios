# Preguntas frecuentes / solucion de problemas

Buscá tu problema por titulo. Si no esta, fijate en el mensaje exacto que te
aparecio en la ventana negra — suele decir que guia mirar.

## "python no se reconoce como un comando"

Windows no encuentra Python. Casi siempre es porque al instalarlo no se
tildo "Add python.exe to PATH".

**Solucion rapida:** volvé a instalar Python desde
https://www.python.org/downloads/ y esta vez tildá esa casilla antes de
apretar "Install Now" (ver
[guia_instalacion_windows.md](guia_instalacion_windows.md#paso-1--instalar-python-312)).

**Si no querés reinstalar:** podes agregarlo a mano siguiendo este video:
**"Cómo agregar Python al PATH en Windows 11 (2026)"**
https://www.youtube.com/watch?v=GmTG25Aoqvk

Despues de cualquiera de las dos opciones, cerrá la consola y abri una
nueva (los cambios de PATH no se ven en ventanas ya abiertas).

## Windows SmartScreen o el antivirus bloquean el .bat

Al hacer doble click en `instalar.bat` (o cualquier otro `.bat`) puede
aparecer un cartel azul "Windows protegió su PC" con un boton "No ejecutar".

Esto pasa porque el archivo es nuevo y pocas personas lo corrieron todavia
— no es un virus, es un script de texto que vos mismo podes abrir y leer
con el Bloc de notas para confirmarlo.

**Solucion:** en el cartel azul, hace click en **"Más información"** y
después en **"Ejecutar de todas formas"**.

Si tu antivirus de la empresa lo bloquea directamente (sin darte la opcion
de "ejecutar igual"), pedile al equipo de IT que agregue una excepcion para
la carpeta del proyecto.

## El puerto 8000 esta ocupado / la pagina no abre

Si al correr `iniciar.bat` ves un error que menciona el puerto 8000 o
"address already in use":

1. Puede que ya tengas la aplicacion abierta en otra ventana — buscá si hay
   otra ventana negra corriendo y usá esa, o cerrala primero.
2. Si no, abri la aplicacion manualmente con otro puerto:
   ```
   .venv\Scripts\python.exe -m sobreprecios.cli servir --puerto 8001
   ```
   y despues entra en el navegador a `http://127.0.0.1:8001`.

## El Boletin Oficial (o datos.gob.ar) esta lento o no responde

Son paginas del Estado, no las maneja esta aplicacion — a veces estan caidas
o lentas. La aplicacion reintenta sola. Si `actualizar_datos.bat` tarda
mucho o falla con un error de conexion:

1. Volvé a intentar mas tarde (a la noche o temprano a la mañana suelen
   andar mejor).
2. Fijate que tengas internet.
3. Si el error persiste varios dias, avisale al equipo tecnico con el
   mensaje de error completo.

## "limite de cuota" / mensaje 429

Esto quiere decir que llegaste al limite de consultas gratis del dia (o del
minuto) en Gemini u OpenRouter. **No es un error de la aplicacion** — es
esperable usando planes gratis, sobre todo si estas analizando muchas
compras de una sola vez.

**Que hacer:**

- Esperá: el limite se resetea solo (por minuto o por dia, segun el
  mensaje).
- Si configuraste una clave de OpenRouter como respaldo (ver
  [guia_claves_api.md](guia_claves_api.md#paso-2-opcional--clave-de-openrouter-como-respaldo)),
  la aplicacion la va a usar automaticamente cuando Gemini este saturado.
- Si pasa todo el tiempo y el trabajo es urgente, se puede pasar a un plan
  de pago (unos centavos por consulta) — ver el final de
  [guia_claves_api.md](guia_claves_api.md#si-mas-adelante-quieren-pasar-a-un-plan-de-pago).

## Como actualizo el proyecto a la ultima version

Si bajaste el proyecto con Git (Opcion B de la guia de instalacion):

1. Abri PowerShell o `cmd` en la carpeta del proyecto.
2. Escribi:
   ```
   git pull
   ```
3. Si cambiaron las dependencias (`requirements.txt`), volvé a correr
   `instalar.bat` para instalarlas.

Si bajaste el ZIP (Opcion A), lo mas simple es volver a descargar el ZIP
nuevo y reemplazar los archivos — **pero antes hace una copia de tu carpeta
`data\` y tu archivo `.env`** para no perder la base ni tus claves (esos dos
no vienen en el ZIP del proyecto, son tuyos).

## Como hago backup de la base

La base de datos es un solo archivo: `data\sobreprecios.db`.

1. Cerrá la aplicacion (cerrá la ventana negra de `iniciar.bat`) antes de
   copiarla, para evitar copiar un archivo a medio escribir.
2. Copiá el archivo `data\sobreprecios.db` a otra carpeta o a un pendrive,
   con un nombre que incluya la fecha, por ejemplo:
   `sobreprecios_backup_2026-10-11.db`.
3. Para restaurar, cerrá la aplicacion, reemplazá `data\sobreprecios.db`
   por tu copia, y volvé a abrir `iniciar.bat`.

Conviene hacer esto antes de correr `actualizar_datos.bat` con un rango de
fechas grande, o antes de pedirle a Claude Code cambios relacionados a
datos.

## Me equivoqué pegando una clave, ¿como la borro o cambio?

Volvé a correr `configurar_claves.bat` y pegá la clave nueva cuando te la
pida — reemplaza automaticamente a la anterior. Si querés dejarla vacia,
abri el archivo `.env` con el Bloc de notas y borrá esa linea a mano (con
cuidado de no tocar las demas).

## ¿Donde pido ayuda si nada de esto funciona?

Juntá el mensaje de error completo (podes seleccionarlo con el mouse en la
ventana negra y copiarlo con Ctrl+C, o sacarle una foto a la pantalla) y
mandaselo a quien te dio acceso al proyecto, junto con en que paso de que
guia estabas.
