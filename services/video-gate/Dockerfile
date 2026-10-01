FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home gate
USER gate
COPY jellyfin.py catalog.py images.py hls.py gate.py ./
# One process (it holds the catalog and the transcode sessions), many threads: every film that streams holds one for its
# whole length. MAX_STREAMS (default 32) of them may stream at once; the other 16 keep the catalog, posters and progress
# reports answering however many films are playing. No --access-logfile, on purpose: the pass is in every URL, and a log
# line would carry it.
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--worker-class", "gthread", "--threads", "48", \
     "gate:from_env()"]
