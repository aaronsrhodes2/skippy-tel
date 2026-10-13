FROM python:3.12-slim
RUN useradd --create-home reporter
USER reporter
WORKDIR /app
COPY reporter.py localinfo.py ./
CMD ["python", "-u", "reporter.py"]
