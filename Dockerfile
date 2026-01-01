# MobilityOne WhatsApp Bot - Final Production Dockerfile
# Verzija: 11.0.2
# Fokus: Stabilnost, sigurnost i optimizacija tokena (cache)

FROM python:3.11-slim

# 1. OSNOVNE POSTAVKE OKOLINE
# PYTHONDONTWRITEBYTECODE: Sprječava pisanje .pyc datoteka (čisti kontejner)
# PYTHONUNBUFFERED: Osigurava da logovi odmah izlaze u konzolu (bitno za Docker logove)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 2. INSTALACIJA SISTEMSKIH PAKETA + TINI
# tini: Ključan za ispravno rukovanje signalima (SIGTERM). Bez njega worker ne staje odmah pri skaliranju.
# postgresql-client: Za init skripte baze podataka.
# curl: Za healthcheck pozive.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    postgresql-client \
    curl \
    tini \
    procps \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

WORKDIR /app

# 3. SIGURNOST I PRIPREMA CACHE-A
# Kreiramo non-root korisnika (appuser) jer je pokretanje bota kao root ogroman rizik.
# Unaprijed kreiramo .cache direktorij kako bismo mu dodijelili vlasništvo prije nego se spoji volumen.
RUN groupadd -r appgroup && useradd -r -g appgroup appuser && \
    mkdir -p /app/.cache && \
    chown -R appuser:appgroup /app

# 4. OPTIMIZACIJA SLOJEVA (Caching)
# Prvo kopiramo samo requirements.txt. Ako se kod mijenja, a biblioteke ne, Docker preskače ovaj spori korak.
COPY --chown=appuser:appgroup requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# 5. KOPIRANJE APLIKACIJE
# Kopiramo cijeli projekt. Ako tvoj .cache folder postoji u root-u, bit će kopiran unutra.
COPY --chown=appuser:appgroup . .

# Osiguravamo da je init skripta izvršna (ako postoji)
RUN chmod +x /app/docker/init-db.sh 2>/dev/null || true

# 6. AKTIVACIJA KORISNIKA
USER appuser

# 7. ENTRYPOINT & COMMAND
# ENTRYPOINT postavlja tini kao PID 1. On će proslijediti signale Pythonu.
ENTRYPOINT ["/usr/bin/tini", "--"]

# Defaultna naredba je za API servis. 
# Worker će u docker-compose.yml ovo zamijeniti s "python worker.py"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]