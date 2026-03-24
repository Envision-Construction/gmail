# syntax=docker/dockerfile:1

# Stage 1: builder — install dependencies into /app/deps
FROM python:3.10-slim@sha256:4ba18b066cee17f2696cf9a2ba564d7d5eb05a91d6a949326780aa7c6912160d AS builder

ENV PYTHONUNBUFFERED=True
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir --target=/app/deps -r requirements.txt


# Stage 2: runtime — non-root user, copy deps and app code
FROM python:3.10-slim@sha256:4ba18b066cee17f2696cf9a2ba564d7d5eb05a91d6a949326780aa7c6912160d AS runtime

ENV PYTHONUNBUFFERED=True
ENV APP_HOME=/app
ENV PYTHONPATH=/app/deps

WORKDIR $APP_HOME

# Copy installed dependencies from builder
COPY --from=builder /app/deps /app/deps

# Copy application source files
COPY gmail_scraper.py .
COPY main.py .

# Run as nonroot (uid 65532)
RUN useradd -u 65532 -r -s /sbin/nologin nonroot
USER nonroot

# Run the web service on container startup.
# Use functions-framework to run the function
CMD exec functions-framework --target=run_scraper --port=8080
