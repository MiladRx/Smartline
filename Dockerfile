FROM python:3.12-alpine
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir aiohttp
CMD ["sh", "-c", "python server.py"]
