# Built by .github/workflows/deploy.yml from the REPO ROOT, so the image can
# include files shared with other parts of the repo:
#   docker build -f portal/Dockerfile .
FROM python:3.12-slim

WORKDIR /app
COPY portal/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY portal/ .
# Shared with the rest of the repo — one source of truth, copied in at build:
COPY services/flask-api/app/skippy_persona.md ./skippy_persona.md
COPY infra/gcp-games/discord-notify.py ./discord_notify.py

ENV PYTHONUNBUFFERED=1
# Long timeout: a restart waits for the VM to stop (worlds saved) and start.
CMD exec gunicorn --bind "0.0.0.0:${PORT:-8080}" --workers 1 --threads 8 --timeout 900 app:app
