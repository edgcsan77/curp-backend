# Dockerfile
FROM python:3.11-slim

# 1) Instalar SOLO Chromium (sin chromium-driver)
RUN apt-get update && apt-get install -y \
    chromium \
    fonts-liberation \
    wget \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 2) Selenium usará este binario de Chrome
ENV CHROME_BIN=/usr/bin/chromium

# 3) Directorio de trabajo
WORKDIR /app

# 4) Dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5) Copiar código
COPY . .

# 6) Ejecutar FastAPI con uvicorn (Render pone $PORT)
CMD ["sh", "-c", "uvicorn api:app --host 0.0.0.0 --port ${PORT:-10000}"]
