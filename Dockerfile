FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY cert_expiry_alert.py .
COPY web_server.py .
COPY entrypoint.sh .

RUN mkdir -p /data

# Port 8080 for the web dashboard
EXPOSE 8080

CMD ["sh", "/app/entrypoint.sh"]
