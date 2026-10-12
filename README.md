# Sobreprecios en compras del Estado

Herramienta local para detectar posibles sobreprecios en compras del Estado nacional argentino de bienes de
góndola (alimentos, limpieza, librería): compara lo que pagó el Estado en cada licitación con lo que costaba el
mismo producto en el mercado (mayoristas, supermercados, INDEC) en la misma fecha.

Corre en tu computadora (Windows o Mac), con una página web local y una base SQLite. No necesita servidores.

## Empezar (Windows, sin saber programar)

1. Instalá Python 3.12 → [guía con video](docs/guia_instalacion_windows.md)
2. Doble click en `instalar.bat`
3. Doble click en `configurar_claves.bat` (clave gratis de IA → [cómo sacarla](docs/guia_claves_api.md))
4. Doble click en `iniciar.bat` → se abre http://127.0.0.1:8000

Problemas: [preguntas frecuentes](docs/preguntas_frecuentes.md). Para seguir mejorando el sistema con IA:
[guía de Claude Code](docs/guia_claude_code.md).

## Qué hace

| Solapa | Para qué |
|---|---|
| **Licitaciones** | Lista de compras analizadas, ordenadas por exceso estimado, con semáforo |
| **Detalle** | Qué se compró, a quién, a qué precio, contra qué referencia, y la evidencia de mercado |
| **Analítica** | Peores organismos, productos, proveedores, procedimientos y meses |
| **Empresas** | Adjudicatarios y su historia societaria (autoridades, socios, domicilios en el tiempo) |
| **Reportes** | Informe para periodistas: página imprimible, Excel y CSV, con metodología y limitaciones |
| **Datos y fuentes** | Actualizar la base (Boletín Oficial, precios de hoy, INDEC) en segundo plano |
| **＋ Investigar** | Analizar un caso puntual: link del BO o de COMPR.AR, número de proceso, texto o PDF |

## Cómo se calcula (resumen)

- Cada precio se lleva a $/kg, $/litro o $/unidad y se compara solo contra el **mismo producto, variedad y envase**
  (catálogo en [config/productos.yml](config/productos.yml)). Si la compra no dice la variedad o el mercado no tiene
  ese producto en la fecha, queda gris: no se compara contra algo distinto.
- Referencia: la **misma marca** si la licitación la nombra; si no, el **mínimo de mercado** (supermercados y
  mayoristas), descartando precios absurdos.
- Precios del día de la compra o anteriores (hasta 15 días), llevados al mes de la compra con el IPC.
- Semáforo: amarillo ≥ 8 % contra el mínimo observado (con 1 mes de plazo de pago descontado). **Rojo** ≥ 20 % contra el
  mínimo **confirmado por dos cadenas distintas**, aun suponiendo 2 meses de plazo. Diferencias > 4× se marcan
  "revisar unidad", nunca rojo. El promedio INDEC solo nunca da rojo.
- Montos de distintos años se pueden ver en **pesos reales** (descontada la inflación) o en **dólares** (contado con
  liqui del día) para sumarlos y compararlos.

**Una brecha no prueba una irregularidad.** Logística, plazos de pago, especificaciones o errores de publicación
pueden explicarla. Cada caso se revisa a mano antes de compartirlo (estado "publicable").

Detalle técnico y decisiones: [DESIGN_sobreprecios.md](DESIGN_sobreprecios.md).

## Línea de comandos

```bash
python -m sobreprecios.cli setup                  # base + catálogo + INDEC (+ dataset 2021 si está)
python -m sobreprecios.cli pipeline --desde 2026-01-01 --hasta 2026-09-30
python -m sobreprecios.cli precios-hoy            # una vez por día
python -m sobreprecios.cli investigar "<link o número>"
python -m sobreprecios.cli servir --puerto 8000
python -m pytest -q                               # tests
```

## Antecedente

Grupo Quijote (Calles, Madanes, Oks, Serebrennik; software de David Ruiz Díaz), "Indicadores de Transparencia en
Argentina – El Precio de los Insumos Críticos que compra el Estado" (2021). Este proyecto reimplementa y extiende
esa metodología; su dataset 2021 se usa como caso de validación.

## Licencia

[MIT](LICENSE) — Copyright (c) 2026 Grupo Quijote. Se puede usar, copiar, modificar y redistribuir libremente,
manteniendo el aviso de copyright y la licencia. Chart.js (`sobreprecios/web/static/vendor/`) conserva su propia
licencia MIT.
