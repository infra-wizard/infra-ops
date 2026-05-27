FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY k8s_tls_expiry_monitor.py .
COPY config.yaml .

# Run a single scan by default; pass --daemon to loop
ENTRYPOINT ["python", "k8s_tls_expiry_monitor.py"]
