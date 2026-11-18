FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PGCONNECT_TIMEOUT=10
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app
COPY src ./src
COPY db ./db
COPY scripts ./scripts
USER app
EXPOSE 8000
CMD ["python", "-m", "scripts.start"]
