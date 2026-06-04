FROM python:3.12-slim

WORKDIR /opt/app

COPY main.py .
RUN pip install --no-cache-dir "python-telegram-bot>=22.0"

ENV PYTHONUNBUFFERED=1
ENV DB_PATH=/data/hookah.db
ENV LOG_LEVEL=INFO
ENV LOG_PATH=/data/logs/bot.log
ENV LOG_BACKUP_WEEKS=4
VOLUME ["/data"]

CMD ["python", "main.py"]
