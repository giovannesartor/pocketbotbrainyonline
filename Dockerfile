# Imagem oficial Playwright + Python — já vem com Chromium e todas as libs
# (libxkbcommon, libnss3, libatk, fontes, etc.) necessárias para rodar headless.
FROM mcr.microsoft.com/playwright/python:v1.49.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    POCKET_HEADLESS=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install chromium

COPY . .

CMD ["python", "main.py"]
