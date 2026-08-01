FROM python:3.12-slim

WORKDIR /opt/app

# Pin the bot framework version to avoid unexpected breaking changes.
ENV PTB_VERSION=22.7

COPY main.py .
RUN pip install --no-cache-dir "python-telegram-bot[job-queue]==${PTB_VERSION}"

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/hookah.db
ENV LOG_LEVEL=INFO
ENV LOG_PATH=/data/logs/bot.log
ENV LOG_BACKUP_WEEKS=4
ENV TIMEZONE=Europe/Belgrade
VOLUME ["/data"]

CMD ["python", "main.py"]
