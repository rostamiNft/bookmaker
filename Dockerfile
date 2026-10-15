# NOTE: provided for convenience; not built/tested in the development sandbox (no Docker there).
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements*.txt backend/
RUN pip install --no-cache-dir -r backend/requirements.txt -r backend/requirements-optional.txt
COPY backend/ backend/
COPY assets/ assets/
COPY --from=ui /ui/dist frontend/dist
ENV BOOKMAKER_DATA_DIR=/data BOOKMAKER_ENV=production
VOLUME /data
EXPOSE 8000
WORKDIR /app/backend
RUN useradd -m bookmaker && mkdir -p /data && chown bookmaker /data
USER bookmaker
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
