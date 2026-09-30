FROM python:3.12-slim
RUN pip install --no-cache-dir flask==3.1.3 gunicorn==23.0.0 mutagen==1.47.0 && useradd --create-home gate
USER gate
WORKDIR /app
COPY library.py gate.py ./
# One process (it holds the catalog and the scan thread), many threads: every listener holds one while a track streams.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--worker-class", "gthread", "--threads", "32", \
     "gate:from_env()"]
