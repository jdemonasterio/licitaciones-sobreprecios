# Imagen para correr el webui en un servicio en la nube (Railway recomendado; ver
# docs/despliegue_nube.md). Sin dependencias de dev ni Playwright: eso es solo para
# el simulador local (tests/e2e), no hace falta en produccion.
FROM python:3.12-slim

# sqlite3: CLI para inspeccionar/respaldar la base a mano con `docker exec` si hace falta
# (la base vive en el volumen montado en /data, no en la imagen).
RUN apt-get update \
    && apt-get install -y --no-install-recommends sqlite3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chmod +x docker-entrypoint.sh

# La base y los caches (BO, LLM, precios) viven en el volumen persistente montado
# en /data: sin esto, un redeploy borra todo lo bajado. Se pueden pisar desde las
# variables de entorno de la plataforma si se monta en otro punto.
ENV LIC_DB_PATH=/data/sobreprecios.db \
    LIC_CACHE_DIR=/data/cache \
    PYTHONUNBUFFERED=1 \
    PORT=8000

RUN mkdir -p /data
VOLUME ["/data"]

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
