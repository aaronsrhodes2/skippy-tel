FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home gate
USER gate
COPY ready.py gate.py ./
# One process (it holds the catalog), many threads: every listener holds one while a book streams. No --access-logfile, on
# purpose: the pass is in every URL, and a log line would carry it. Nothing here logs a title either.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--worker-class", "gthread", "--threads", "16", "gate:from_env()"]
