# wa-trickle: a slow, free Wolfram|Alpha vs Mentat comparison queue (services/wa-trickle/trickle.py).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY trickle.py .
# Runs as a non-root user; the only writable place is /state (the queue's SQLite file).
RUN useradd --create-home trickle && mkdir -p /state && chown trickle /state
USER trickle
ENV PYTHONUNBUFFERED=1 WA_STATE=/state
# One worker: the queue thread lives in the process.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "120", "trickle:build_app()"]
