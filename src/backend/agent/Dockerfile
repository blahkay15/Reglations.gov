# AgentCore Runtime runs linux/arm64. Build with:
#   docker buildx build --platform linux/arm64 -f src/backend/agent/Dockerfile -t <uri>:latest --push .
FROM public.ecr.aws/docker/library/python:3.12-slim

WORKDIR /app
COPY src/backend/agent/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
COPY src/backend/agent/main.py src/backend/agent/tools.py src/backend/agent/prompt.py /app/

CMD ["python", "main.py"]
