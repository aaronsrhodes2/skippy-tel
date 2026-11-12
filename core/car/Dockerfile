# The car reader (car.py): TeslaMate's MQTT state as JSON on 7351, for skippy-pc's home reporter. Read only.
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ready.py car.py ./
EXPOSE 7351
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7351/car', timeout=2)"
CMD ["python", "-u", "car.py"]
