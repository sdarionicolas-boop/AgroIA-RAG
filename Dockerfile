FROM python:3.10-slim

WORKDIR /app

# Instalaciones críticas del sistema operativo para agro/geospatial/vision
RUN apt-get update && apt-get install -y \
    postgresql-client \
    gdal-bin \
    libgdal-dev \
    libgl1 \
    libglib2.0-0 \
    gcc \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiamos código y configuración
COPY src/ src/
COPY config/ config/

# Variables de entorno
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Puerto por defecto para Streamlit (se sobreescribirá en compose si es necesario)
EXPOSE 8501
EXPOSE 8000

CMD ["streamlit", "run", "src/streamlit_app.py", "--server.address=0.0.0.0"]
