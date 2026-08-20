FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg aria2 ca-certificates gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY bot/requirements.txt /app/bot/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/bot/requirements.txt

COPY bot /app/bot
COPY authorized_users.json /app/authorized_users.json
RUN mkdir -p /app/data /tmp/downloads && chmod 777 /tmp/downloads

VOLUME ["/app/data", "/app/bot/cookies.txt"]
EXPOSE 8000

CMD ["python", "-u", "bot/main.py"]