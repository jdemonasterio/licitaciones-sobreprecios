# Auditoría del juez (gpt-6-astra) — 2026-10-12 15:30

Ítems: 3 · gasto LLM aprox. USD 0.42 (tope 5.0)

El juez puede equivocarse: revisar items.tsv y completar 'Tu veredicto'. Medir acuerdo juez-humano antes de
usarlo como métrica. La muestra es estratificada (incluye verdes y grises): las tasas no son globales.

## Veredictos por capa

| Capa | ok | error | dudoso | sin juicio |
|---|---|---|---|---|
| comparacion | 1 | 1 | 0 | 0 |
| extraccion | 0 | 0 | 1 | 0 |

## Errores por tipo

- referencia_inadecuada: 1

## Reglas sugeridas (candidatas a backlog)

- [comparacion / referencia_inadecuada] Exigir envase exacto siempre (renglon:42)
