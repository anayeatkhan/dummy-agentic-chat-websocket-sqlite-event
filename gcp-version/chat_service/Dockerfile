# Build context must be the project ROOT (gcp-version/), not this folder.
# docker build -f chat_service/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cached separately from source code)
COPY chat_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared package (needed by chat_service imports)
COPY shared/ ./shared/

# Copy service source
COPY chat_service/ ./chat_service/

# Cloud Run injects PORT env var; default to 8002 for local use
ENV PORT=8002

CMD uvicorn chat_service.main:app --host 0.0.0.0 --port ${PORT}
