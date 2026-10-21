FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/app/data
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd -m reviewer && mkdir data && chown reviewer:reviewer data
COPY reviewbot/ ./reviewbot/
COPY web/ ./web/
COPY examples/ ./examples/
USER reviewer
EXPOSE 8020
CMD ["python", "-m", "uvicorn", "reviewbot.app:app", "--host", "0.0.0.0", "--port", "8020", "--workers", "1"]
