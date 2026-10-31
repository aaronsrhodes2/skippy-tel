# skippy-pc as a Spotify Connect speaker (services/spotify/README.md): go-librespot (unofficial, Premium only) writes its PCM into a FIFO, and
# service.py relays it to the hub's audio router over TCP, holds the Captain's Spotify Web API sign-in, and answers the hub and purple Skippy.
FROM ghcr.io/devgianlu/go-librespot:v0.10.5@sha256:0320b2dcbbb68ab8c170de77f025c7e50d269bccfd75eafcfcac4032bdd016f4
RUN apk add --no-cache python3
WORKDIR /app
COPY ready.py service.py ./
CMD ["python3", "-u", "service.py"]
