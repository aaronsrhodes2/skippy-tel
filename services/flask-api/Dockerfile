FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
EXPOSE 5000
CMD ["gunicorn", "--worker-class", "gevent", "--workers", "1", "--threads", "8", "--bind", "0.0.0.0:5000", "--timeout", "120", "--access-logfile", "-", "--access-logformat", "%(h)s %(r)s %(s)s %(b)sb %(L)ss \"%(a)s\"", "app.main:app"]
