FROM python:3.11-slim

# Systeem dependencies: smbclient voor SMB-opslag, mediainfo voor kwaliteitscheck
RUN apt-get update && apt-get install -y \
    smbclient \
    mediainfo \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Data map aanmaken (wordt overschreven door volume mount)
RUN mkdir -p /app/data

EXPOSE 8080

CMD ["python", "app.py"]
