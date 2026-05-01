FROM python:3.10-slim

# --- Environment ---
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

WORKDIR /app

# --- System dependencies (Playwright + PostgreSQL + build) ---
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    curl \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libc6 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libexpat1 \
    libfontconfig1 \
    libgbm1 \
    libgcc-s1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libstdc++6 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libxrender1 \
    libxshmfence1 \
    libxss1 \
    libxtst6 \
    xdg-utils \
    && rm -rf /var/lib/apt/lists/*

# --- Install Python deps FIRST (cache-friendly) ---
COPY requirements.txt .

RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# --- Install Playwright browsers ---
RUN python -m playwright install chromium

# --- Copy application ---
COPY . .

# --- Expose ---
EXPOSE 8000

# --- Run ---
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]


