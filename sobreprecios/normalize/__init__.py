"""Normalización de entidades: CUIT, empresas, personas y organismos.

Reglas (decisiones del LLM prime P13/P14, 2026-10-11, pendientes de check de Mateo):
- Una empresa se identifica por CUIT válido. Sin CUIT, el nombre normalizado solo
  genera CANDIDATOS de unión (nunca une solo).
- Una persona se identifica por DNI. Sin DNI, los tokens del nombre son una pista,
  no una identidad (homónimos reales vistos: HOLOWATY).
- Un organismo se guarda con el nombre de la fecha. El código SAF se registra pero
  NO une renombres (Desarrollo Social / Capital Humano) por sí solo.
Siempre se conserva el texto original al lado del normalizado.
"""
