FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# The build directory is staged by infra/deploy.ps1, which copies the canonical
# policy.yaml and the shared modules in alongside this service's own files so
# that the authority model cannot drift between the engine and the agents.
COPY . ./

ENV PORT=8080
CMD exec uvicorn main:app --host 0.0.0.0 --port ${PORT} --workers 1
