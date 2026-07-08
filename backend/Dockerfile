# Salescale backend — production image.
# Runs the FastAPI app under uvicorn. Schema migrations run automatically on
# startup (alembic upgrade head), so a fresh Postgres/Supabase database is
# brought fully up to date on first boot.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching. Upgrade pip/setuptools first —
# the base image's setuptools has known (build-time) CVEs.
COPY requirements.txt .
RUN pip install --upgrade pip setuptools && pip install -r requirements.txt

# App code (see .dockerignore for exclusions: venv, .env, dist, tests…).
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

# Hosts commonly inject $PORT; default to 8000 locally.
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
