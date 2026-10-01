FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# База лежит в /data. ПОДКЛЮЧИ туда постоянный диск (volume) на хостинге,
# иначе все задачи пропадут при перезапуске контейнера.
ENV DB_PATH=/data/tasks.db
VOLUME /data

CMD ["python", "bot.py"]
