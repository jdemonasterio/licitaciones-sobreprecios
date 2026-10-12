# Guia de instalacion en Windows

Esta guia te lleva de "no tengo nada instalado" a "tengo la aplicacion abierta en
el navegador". Son 4 pasos. Cada paso dice que tenes que ver en pantalla para
saber que salio bien.

Si algo no coincide con lo que ves, saltá a [preguntas_frecuentes.md](preguntas_frecuentes.md)
al final de esta guia.

## Antes de empezar

- Windows 10 u 11.
- Conexion a internet (vas a descargar unos 100-200 MB en total).
- 15-20 minutos la primera vez.

## Paso 1 — Instalar Python 3.12

El programa esta escrito en Python. Si ya tenes Python 3.12 instalado podes
saltar a la seccion "Como saber si ya lo tengo".

1. Entra a **https://www.python.org/downloads/** desde el navegador.
2. Hace click en el boton grande amarillo que dice "Download Python 3.12.x"
   (si te ofrece una version mas nueva como 3.13, tambien funciona, pero si
   podes elegir, preferi 3.12).
3. Abri el archivo descargado (termina en `.exe`, suele quedar en la carpeta
   "Descargas").
4. **Esto es lo mas importante de todo el paso:** en la primera pantalla del
   instalador, ABAJO DE TODO, hay un tilde que dice
   **"Add python.exe to PATH"**. Tildalo ANTES de tocar nada mas.

   Que tenes que ver en pantalla: una ventana gris con el logo de Python,
   un tilde sin marcar que dice "Add python.exe to PATH", y mas abajo un
   boton "Install Now".

5. Tildá esa casilla y despues hace click en "Install Now".
6. Esperá a que la barra llegue al 100% y aparezca "Setup was successful".
7. Cerrá el instalador.

Si te olvidaste de tildar la casilla, no pasa nada, pero despues Windows no va
a encontrar el comando `python` — la solucion esta en
[preguntas_frecuentes.md](preguntas_frecuentes.md#python-no-se-reconoce-como-comando).

### Video (verificado, 2026)

**"Cómo agregar Python al PATH en Windows 11 (2026)"** — canal *divcode*
https://www.youtube.com/watch?v=GmTG25Aoqvk

### Como saber si ya lo tengo

1. Apretá la tecla Windows, escribi `cmd` y apretá Enter (se abre una ventana
   negra, la "consola").
2. Escribi `python --version` y apretá Enter.
3. Si ves algo como `Python 3.12.7`, ya esta instalado: pasá al Paso 2.
4. Si ves `'python' no se reconoce como un comando...`, instalalo siguiendo
   los pasos de arriba.

## Paso 2 — Conseguir la carpeta del proyecto

Hay dos formas. Si no sabes que es Git, usa la Opcion A (es mas simple).

### Opcion A — Descargar el ZIP (recomendada si no usaste Git nunca)

1. Entra a la pagina del proyecto en GitHub (te la va a pasar quien te dio
   esta guia).
2. Hace click en el boton verde "Code" y despues en "Download ZIP".
3. Cuando termine de bajar, hace click derecho sobre el archivo `.zip` y
   elegi "Extraer todo..." (o "Extract All...").
4. Elegi una carpeta facil de encontrar, por ejemplo
   `C:\Usuarios\TuNombre\licitaciones-sobreprecios`, y confirma.
5. Abri esa carpeta: tiene que tener adentro archivos como `instalar.bat`,
   `requirements.txt` y una carpeta `sobreprecios`.

### Opcion B — Clonar con Git (si ya instalaste Git)

Si vas a usar Claude Code mas adelante (ver
[guia_claude_code.md](guia_claude_code.md)), conviene esta opcion porque vas a
poder actualizar el proyecto con un solo comando.

1. Si todavia no tenes Git, instalalo desde https://git-scm.com/downloads/win
   (dejá todas las opciones del instalador en su valor por defecto — apretá
   "Next" varias veces y al final "Install" y "Finish").

   Video (verificado): **"Instalación de Git en Windows paso a paso"** — canal
   *Programmer Not Found - 404 Error*
   https://www.youtube.com/watch?v=cYLapo1FFmA

2. Abri la consola (tecla Windows, escribi `cmd`, Enter).
3. Navega a donde quieras guardar el proyecto, por ejemplo:
   ```
   cd Desktop
   ```
4. Clona el proyecto (te van a pasar la URL real):
   ```
   git clone <URL-del-repositorio>
   ```
5. Entra a la carpeta creada:
   ```
   cd licitaciones-sobreprecios
   ```

## Paso 3 — Instalar la aplicacion

1. Abri la carpeta del proyecto en el Explorador de Windows.
2. Hace doble click en **`instalar.bat`**.
3. Se abre una ventana negra que va mostrando pasos del 1 al 5. Puede tardar
   unos minutos (instala paquetes de Python y crea la base de datos).

   Que tenes que ver en pantalla: lineas como
   `[1/5] Buscando Python 3.12...`, `[2/5] Creando entorno virtual...`, etc.,
   y al final `Listo! Proximos pasos:`.

4. Si en algun punto aparece la palabra `ERROR`, leé el mensaje: te va a decir
   que guia mirar. No cierres la ventana todavia, saca una foto o copiá el
   texto si vas a pedir ayuda.
5. Cuando termine, apretá cualquier tecla para cerrar la ventana (el mensaje
   `pause` te lo va a pedir).

Windows puede mostrar un cartel azul de "Windows protegió su PC" (SmartScreen)
antes de dejarte correr el `.bat` — es normal en un script nuevo, no es un
virus. Solucion en
[preguntas_frecuentes.md](preguntas_frecuentes.md#windows-smartscreen-o-el-antivirus-bloquean-el-bat).

## Paso 4 — Cargar tus claves y arrancar

1. Hace doble click en **`configurar_claves.bat`**. Te va a preguntar tu
   clave de Gemini (y, opcional, de OpenRouter/OpenAI). Si todavia no la
   tenes, apretá Enter para saltear — podes volver a correr este script
   despues. Pasos para conseguir la clave:
   [guia_claves_api.md](guia_claves_api.md).
2. Hace doble click en **`iniciar.bat`**.
3. Se abre una ventana negra y, a los pocos segundos, el navegador con la
   aplicacion en `http://127.0.0.1:8000`.

   Que tenes que ver en pantalla: la ventana negra dice algo como
   `Uvicorn running on http://127.0.0.1:8000`, y el navegador muestra la
   pagina de la aplicacion.

4. **No cierres la ventana negra** mientras usas la aplicacion — es el motor
   que la hace funcionar. Para apagarla, cerrá esa ventana.

¡Listo! La proxima vez que quieras usar la aplicacion, solo hace falta doble
click en `iniciar.bat` (no hace falta repetir `instalar.bat`).

## Troubleshooting rapido

Para todo lo demas (puerto ocupado, el Boletin Oficial no responde, limite de
cuota del plan gratis, como actualizar, como hacer backup de la base), ver
[preguntas_frecuentes.md](preguntas_frecuentes.md).
