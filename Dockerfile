# Dockerfile SIN Chromium
FROM python:3.11-slim

# 1) Directorio de trabajo
WORKDIR /app

# 2) Dependencias del sistema (solo lo necesario para osmnx/psycopg2)
RUN apt-get update && apt-get install -y \
    build-essential \
    gdal-bin \
    libgdal-dev \
    && rm -rf /var/lib/apt/lists/*

# 3) Dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4) Copiar código
COPY . .

# 5) Ejecutar FastAPI con uvicorn
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}"]
