FROM mirror.gcr.io/library/python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --default-timeout=300 --retries 10 --no-cache-dir -r requirements.txt

COPY app/ ./app/

EXPOSE 8098

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8098", "--workers", "2"]
