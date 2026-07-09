FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY mcp_server ./mcp_server
# Generous timeout + retries: PyPI wheel downloads read-timeout on flaky
# networks and fail the whole build (pip's default timeout is 15s).
RUN pip install --no-cache-dir --timeout 120 --retries 10 .

EXPOSE 8003
CMD ["uvicorn", "mcp_server.__main__:app", "--host", "0.0.0.0", "--port", "8003"]
