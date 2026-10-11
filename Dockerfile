# Runs on a Hugging Face Space (Docker SDK, port 7860) or anywhere with Docker.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LOG_JSON=true

# Spaces run the container as uid 1000.
RUN useradd -m -u 1000 app
WORKDIR /home/app/nightwarden

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY --chown=app:app . .
USER app

EXPOSE 7860
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers"]
