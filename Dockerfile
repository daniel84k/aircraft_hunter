FROM python:3.12-slim

WORKDIR /app
ENV PYTHONPATH=/app/src

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
EXPOSE 9999
CMD ["python", "src/main.py"]
