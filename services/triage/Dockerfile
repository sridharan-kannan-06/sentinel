FROM ollama/ollama:latest

# The base image sets an entrypoint that runs ollama directly. This container
# runs a web service that happens to have ollama behind it, so the entrypoint is
# cleared and start.sh brings both up.
ENTRYPOINT []

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-pip curl \
 && rm -rf /var/lib/apt/lists/*

# The model is baked into the image rather than pulled at boot. A cold start that
# downloads 300MB before it can answer is a cold start that fails its health
# check, and pulling on every instance would also mean the boundary reaching out
# to the internet at runtime.
ENV OLLAMA_HOST=127.0.0.1:11434
RUN ollama serve & \
    for i in $(seq 1 40); do curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break; sleep 1; done; \
    ollama pull gemma3:4b && \
    pkill -f "ollama serve" || true

WORKDIR /app
COPY requirements.txt ./
RUN pip3 install --break-system-packages --no-cache-dir -r requirements.txt

COPY . ./
RUN chmod +x /app/start.sh

ENV PORT=8080
CMD ["/app/start.sh"]
