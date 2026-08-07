# Theta — container image.
#
# Built on Microsoft's Playwright image because it already carries Chromium and
# the ~40 system libraries it needs. Installing those on python:slim by hand is
# a long, brittle apt incantation that breaks every base-image bump.
FROM mcr.microsoft.com/playwright/python:v1.49.0-noble

WORKDIR /app

# Dependencies first, for layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt \
    && python -m playwright install chromium

# App code.
COPY . .

# Runs, playbooks, workspace files and the encryption key live here.
RUN mkdir -p /app/data/runs /app/data/playbooks /app/data/workspace /app/data/briefs \
    && chmod -R 777 /app/data

ENV THETA_HOST=0.0.0.0 \
    THETA_PORT=7860 \
    THETA_COOKIE_SECURE=1 \
    THETA_BROWSER_HEADLESS=1 \
    PYTHONUNBUFFERED=1

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:7860/api/health', timeout=4).status==200 else 1)"

CMD ["python", "app.py"]
