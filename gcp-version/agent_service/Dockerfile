# Build context must be the project ROOT (gcp-version/), not this folder.
# docker build -f agent_service/Dockerfile .

FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (layer cached separately from source code)
COPY agent_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy shared package (needed by agent_service imports)
COPY shared/ ./shared/

# Copy service source
COPY agent_service/ ./agent_service/

# Cloud Run injects PORT env var; default to 8001 for local use
ENV PORT=8001

CMD uvicorn agent_service.main:app --host 0.0.0.0 --port ${PORT}
