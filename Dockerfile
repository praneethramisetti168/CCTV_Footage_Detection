FROM python:3.11-slim

# System deps for OpenCV + YOLOv8
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxrender1 \
    libxext6 \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY backend/ ./backend/
COPY dashboard/ ./dashboard/
COPY pipeline/ ./pipeline/

# Copy data files
COPY store_layout.json ./store_layout.json
COPY pos_transactions.csv ./pos_transactions.csv

# Create data directory for SQLite persistence
RUN mkdir -p /data /clips /events_output

ENV PYTHONPATH=/app/backend:/app
WORKDIR /app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; port=int(os.environ.get('PORT', '8000')); urllib.request.urlopen(f'http://localhost:{port}/health')" || exit 1

CMD ["sh", "-lc", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
