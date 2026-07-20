# Build from the REPO ROOT (not ./backend) so prototype-runtime/ is in context:
#   docker build -t sprntly-backend -f backend/Dockerfile .
# The design agent vite-builds every generated prototype from the repo-root
# prototype-runtime/ scaffold (app/design_agent/storage.py resolves it at
# /prototype-runtime inside the container), so the image needs Node + the
# scaffold with its node_modules preinstalled — without them every prototype
# generation fails with FileNotFoundError/ViteBuildError.
FROM python:3.11-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Install OS-level libs Chromium needs (python:3.11-slim now ships Debian Trixie where
# ttf-unifont/ttf-ubuntu-font-family are renamed; install the real runtime deps manually
# so playwright install chromium works on this image without --with-deps).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libglib2.0-0 libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxcb1 libxkbcommon0 libx11-6 libxcomposite1 libxdamage1 \
    libxext6 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2 \
    libatspi2.0-0 libexpat1 \
    && rm -rf /var/lib/apt/lists/*
RUN python -m playwright install chromium

# Node 20 (npx vite build / npx tsc at generation time)
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

# The vite scaffold every prototype build copies from + symlinks node_modules of.
COPY prototype-runtime /prototype-runtime
RUN cd /prototype-runtime && npm ci --no-audit --no-fund

COPY backend/app ./app
# Vendored PM skills (app/skills/loader.py resolves them at REPO_ROOT/skills =
# /app/skills). Without them /v1/ask/skills is empty and every skill-bound
# agent call (prd-author, weekly-brief, …) raises UnknownSkillError.
COPY backend/skills ./skills

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
