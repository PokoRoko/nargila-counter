FROM python:3.12-slim

ARG GITHUB_REPO
ARG GITHUB_REF=main

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt
RUN test -n "$GITHUB_REPO" && git clone --depth 1 --branch "$GITHUB_REF" "$GITHUB_REPO" app

WORKDIR /opt/app
RUN pip install --no-cache-dir "python-telegram-bot>=22.0"

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/hookah.db
ENV LOG_LEVEL=INFO
VOLUME ["/data"]

CMD ["python", "main.py"]
