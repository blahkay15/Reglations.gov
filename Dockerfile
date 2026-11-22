FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt boto3
COPY regs_pull regs_pull
COPY api api
COPY worker worker

# The task definition overrides JOB_ID, USER_ID, and DOCUMENT_ID per run.
CMD ["python", "-m", "worker.main"]
