FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# curl is used by healthchecks and the backup script; asyncpg and Pillow both ship arm64 and
# amd64 wheels, so no compiler is required.
#
# tesseract reads the collector number off a rectified card (TASK-019). Only the English data
# pack is installed: the number is digits and a slash, so nothing else earns its size.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl tesseract-ocr tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

RUN useradd -m -u 1000 tcg && mkdir -p /data/images && chown -R tcg:tcg /data /srv
USER tcg

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
