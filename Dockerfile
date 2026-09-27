FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot/ bot/
COPY dataset/ dataset/
RUN python dataset/generate_dataset.py --seed-dir dataset --out expanded
EXPOSE 8080
# Exactly one worker: contexts, suppression keys and conversations live in process memory.
CMD ["sh", "-c", "uvicorn bot.app:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
