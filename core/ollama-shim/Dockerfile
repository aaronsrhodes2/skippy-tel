# SkippyCore's Ollama shim (shim.py): Ollama's API on 11434, llama-swap behind it.
# gthread workers: a streamed answer holds its thread for minutes, so a few threads per worker.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY shim.py .
EXPOSE 11434
HEALTHCHECK --interval=15s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:11434/api/version', timeout=2)"
CMD ["gunicorn", "--worker-class", "gthread", "--workers", "2", "--threads", "16", "--bind", "0.0.0.0:11434", "--timeout", "900", "--access-logfile", "-", "shim:app"]
