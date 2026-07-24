FROM python:3.12-slim

WORKDIR /app

# Dashboard uchun fontlar
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Database volume
VOLUME ["/app/data"]
ENV DB_PATH=/app/data/movies.db

CMD ["python", "main.py"]
