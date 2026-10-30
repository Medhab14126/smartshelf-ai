FROM python:3.12-slim

ARG INSTALL_PADDLEOCR=false
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract is the default OCR engine; libglib is needed by OpenCV.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-eng libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt \
    && if [ "$INSTALL_PADDLEOCR" = "true" ]; then pip install paddlepaddle==3.0.0 paddleocr==3.0.0; fi

COPY . .
RUN useradd --create-home --uid 1000 app && mkdir -p /data/uploads && chown -R app:app /data /app
USER app

EXPOSE 8000
# Apply migrations, then start the API (which also runs the scheduler when RUN_SCHEDULER=true).
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
