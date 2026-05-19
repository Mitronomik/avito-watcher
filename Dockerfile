# -- Stage 1: builder -------------------------------------------------------
FROM python:3.12-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Download camoufox browser binary.
# Verify the actual storage path before finalizing the COPY in runtime stage.
RUN python -m camoufox fetch \
 && python -c "import camoufox, os; p=os.path.expanduser('~/.cache/camoufox'); \
    assert os.path.isdir(p), f'camoufox binary not found at {p!r} — update COPY path in runtime stage'"

# -- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime system libs for headless Chromium (nodriver) and Firefox (camoufox).
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libx11-6 \
    libglib2.0-0 \
    libnss3 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxtst6 \
    libgbm1 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy camoufox browser binary from builder.
# If the assert above passed, this path is correct.
COPY --from=builder /root/.cache/camoufox /root/.cache/camoufox

# nodriver downloads Chromium lazily on first run — no pre-seeding needed.

COPY . .

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=20s \
  CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
