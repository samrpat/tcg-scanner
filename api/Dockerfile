FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# Tesseract is only ever called to read a collector number off a rectified card, which is a
# step in *identification*. A scanner-mode build never identifies anything, so it never calls
# it, and installing it there costs ~90 MB of image and a slow apt step on every Pi rebuild
# for a binary that is never executed.
#
# Off by default; `docker-compose.full.yml` builds with it on. The Python binding stays in
# requirements either way — importing pytesseract does not need the binary, only calling it
# does, so the code is identical in both builds and the difference is purely the image.
ARG WITH_OCR=0

# curl is used by the backup script and for hand-checking the API from inside the container.
# asyncpg and Pillow both ship arm64 and amd64 wheels, so no compiler is required.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && if [ "$WITH_OCR" = "1" ]; then \
         apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng; \
       fi \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m -u 1000 tcg \
    && mkdir -p /data/images /data/art-cache /data/thumbnails \
    && chown -R tcg:tcg /data /srv \
    && chmod +x /srv/entrypoint.sh
USER tcg

# Labels a registry and `docker inspect` can read. Overridden at build time by the release
# script so a published image says which commit it came from.
ARG VERSION=dev
ARG REVISION=unknown
ENV TCG_VERSION=$VERSION
LABEL org.opencontainers.image.title="TCG Scanner" \
      org.opencontainers.image.description="Self-hosted trading-card scanner: rectified card photographs, corner close-ups, batch export." \
      org.opencontainers.image.licenses="SEE LICENSE IN LICENSE" \
      org.opencontainers.image.version=$VERSION \
      org.opencontainers.image.revision=$REVISION

EXPOSE 8000
# The schema is brought up to date here, so a fresh install has nothing to run by hand.
ENTRYPOINT ["/srv/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
