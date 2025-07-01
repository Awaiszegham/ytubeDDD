# Use Python 3.11
FROM python:3.11-slim

# Install FFmpeg and clean up
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Create a non-root user for security
RUN useradd --create-home --shell /bin/bash app && \
    chown -R app:app /app
USER app

# Use shell form to interpret environment variables
CMD gunicorn main:app --bind 0.0.0.0:${PORT:-5000} --timeout 300 --workers 2 --max-requests 1000 --max-requests-jitter 100

