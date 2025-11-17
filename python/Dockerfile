FROM python:3.11-slim-bullseye

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    apt-transport-https \
    gnupg \
    unixodbc-dev \
    unixodbc \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Install ODBC Driver 17 for SQL Server
RUN curl https://packages.microsoft.com/keys/microsoft.asc | apt-key add - \
    && curl https://packages.microsoft.com/config/debian/11/prod.list > /etc/apt/sources.list.d/mssql-release.list \
    && apt-get update \
    && echo msodbcsql17 msodbcsql/ACCEPT_EULA boolean true | debconf-set-selections \
    && apt-get install -y msodbcsql17 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python3", "main.py"]
