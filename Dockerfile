FROM python:3.12.9-alpine

# Install system dependencies including FFmpeg, git, and build tools
RUN apk add --no-cache \
    ffmpeg \
    curl \
    unzip \
    git \
    gcc \
    deno \
    musl-dev \
    linux-headers \
    && rm -rf /var/cache/apk/*

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install yt-dlp from source
RUN pip install git+https://github.com/yt-dlp/yt-dlp.git /tmp/yt-dlp.git

# Create non-root user for security
RUN adduser -D -u 1000 botuser && chown -R botuser:botuser /app

# Copy the rest of the application
COPY . .

# Change to non-root user
USER botuser

# Run the bot
CMD ["python", "main.py"]