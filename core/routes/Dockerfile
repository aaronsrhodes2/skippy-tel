# The road door (routes.py): Valhalla's travel times and Nominatim's addresses, both on this box, as one small JSON door on 7354.
FROM python:3.12-slim
WORKDIR /app
COPY ready.py routes.py ./
EXPOSE 7354
# Up is enough for Docker (the deploy's --wait): the first start's map import takes about an hour, and /health says 503 until it's done.
HEALTHCHECK --interval=30s --timeout=5s CMD ["python", "routes.py", "--alive"]
CMD ["python", "-u", "routes.py"]
