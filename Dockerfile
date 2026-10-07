# Entorno reproducible para el proyecto. Fija Python y las librerías para que
# rebuild / tests / dashboard den el mismo resultado en cualquier máquina.
# (snapshot.py depende de la web en vivo y por eso NO es determinista; todo lo
# demás se reconstruye desde los crudos versionados en data/raw/.)
FROM python:3.11-slim-bookworm

# Evita prompts y .pyc; salida de logs sin buffer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar dependencias primero (capa cacheable).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar el resto del proyecto.
COPY . .

# Por defecto, demuestra la reproducibilidad de punta a punta:
# reconstruye la base desde los crudos, corre los tests y regenera los gráficos.
CMD ["sh", "-c", "python rebuild.py && python -m pytest -q && python dashboard.py && echo 'OK — base, tests y gráficos reproducidos'"]
