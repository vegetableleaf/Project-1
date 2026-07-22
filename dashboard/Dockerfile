# Tiny image that serves the money_agent dashboard. It only needs Flask +
# Python's built-in sqlite3 -- no torch, no web3, no secrets.
FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 8000
ENV PORT=8000

CMD ["python", "app.py"]
