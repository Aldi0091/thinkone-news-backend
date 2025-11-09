FROM python:3.11-slim

# System deps that help build wheels (telethon ok without, but good to have)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# If you have requirements.txt, use it. Otherwise pip install inline.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Your app code
COPY . /app

# Expose backend port
EXPOSE 8068

# Run uvicorn (no reload in prod)
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8068"]
