FROM python:3.12-slim
# The house's Leviton backend (services/satellite/leviton.py, mounted at /srv/services): the My Leviton cloud's client.
RUN pip install --no-cache-dir decora_wifi==1.5
RUN useradd --create-home reporter
USER reporter
WORKDIR /app
COPY reporter.py localinfo.py doctor_checks.py local_api.py ./
CMD ["python", "-u", "reporter.py"]
