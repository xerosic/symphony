FROM python:3.12.9-alpine

# Install runtime deps (keep) + temporary build deps (remove later)
RUN apk add --no-cache \
        ffmpeg \
        opus \
        curl \
        unzip \
        git \
        deno \
    && apk add --no-cache --virtual .build-deps \
        gcc \
        musl-dev \
        linux-headers \
        opus-dev

# Set working directory
WORKDIR /app

# Copy requirements first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt \
    && apk del .build-deps

# Create non-root user for security
RUN adduser -D -u 1000 botuser && chown -R botuser:botuser /app

# Copy the rest of the application
COPY . .

# Change to non-root user
USER botuser

# Run the bot
CMD ["python", "main.py"]