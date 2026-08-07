# Theta — container image (works locally and on Hugging Face Spaces / Docker SDK).
FROM python:3.12-slim

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code.
COPY . .

# Bind to all interfaces on the platform's port (HF Spaces expects 7860).
ENV THETA_HOST=0.0.0.0 \
    THETA_PORT=7860 \
    THETA_COOKIE_SECURE=1

EXPOSE 7860

CMD ["python", "app.py"]
