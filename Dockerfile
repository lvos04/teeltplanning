# Twee stappen zoals Gewichtsverwachting: eerst wheels bouwen, daarna een slanke runtime-image.
FROM python:3.12.14-slim-bookworm AS wheels
WORKDIR /build
COPY requirements.txt constraints.txt ./
# Een eigen CA (bijv. achter een bedrijfsproxy) alleen via een build-secret, nooit in de image of de repo.
RUN --mount=type=secret,id=build_ca,target=/run/secrets/build-ca.pem \
    if [ -f /run/secrets/build-ca.pem ]; then PIP_CERT=/run/secrets/build-ca.pem pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt; else pip wheel --no-cache-dir --wheel-dir=/wheels -r requirements.txt; fi

FROM python:3.12.14-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data TZ=Europe/Amsterdam HOME=/tmp
WORKDIR /app
# LibreOffice Calc zonder schermonderdelen voor pdf-afdrukken (BOUWPLAN §9.3, route B) plus lettertypen die
# metrisch gelijk zijn aan Arial/Times/Courier (Liberation) en Calibri/Cambria (Carlito/Caladea).
# Welke lettertypen het echte werkboek gebruikt is open vraag 13; ontbrekende lettertypen kunnen later
# naar /data/lettertypen/ worden geüpload.
RUN --mount=type=bind,from=wheels,source=/wheels,target=/wheels \
    apt-get update \
    && apt-get install -y --no-install-recommends libreoffice-calc-nogui fonts-liberation2 fonts-crosextra-carlito fonts-crosextra-caladea fonts-dejavu-core tzdata \
    && rm -rf /var/lib/apt/lists/* /usr/share/doc/* /usr/share/man/* \
    && pip install --no-cache-dir --no-index /wheels/* \
    && groupadd -g 568 teelt && useradd -u 568 -g 568 -M -d /tmp teelt \
    && mkdir -p /data && chown 568:568 /data
COPY --chown=568:568 . .
USER 568:568
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "teeltplanning.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
