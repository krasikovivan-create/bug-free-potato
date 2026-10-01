FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# База лежит в /data. ПОДКЛЮЧИ туда постоянный диск (volume): иначе после перезапуска
# бот забудет, что уже присылал, и отправит всё заново.
ENV DB_PATH=/data/scraper.db
VOLUME /data

# config.toml монтируется снаружи:  -v $(pwd)/config.toml:/app/config.toml:ro
CMD ["python", "scraper.py", "run"]
