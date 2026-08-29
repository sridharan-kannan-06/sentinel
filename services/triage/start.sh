#!/usr/bin/env bash
set -e

# Ollama serves the model on localhost only. Nothing outside this container can
# reach it, which is the point: the model runs inside the boundary.
ollama serve &

for attempt in $(seq 1 40); do
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "ollama ready after ${attempt} attempt(s)"
    break
  fi
  sleep 1
done

exec uvicorn main:app --host 0.0.0.0 --port "${PORT:-8080}" --workers 1
