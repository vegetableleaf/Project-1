# Deploy the x402 service to any container host (Render / Railway / Fly.io).
# Build context is the PROJECT ROOT:
#     docker build -f deploy/Dockerfile -t money-agent-x402 .
#
# The image is LEAN (no torch). "market_signal" needs a model; without one it
# returns "unavailable" while the other services work. To serve the model too,
# add `torch numpy` to deploy/requirements.txt and COPY a checkpoint.pth below.
FROM python:3.12-slim

WORKDIR /app

COPY deploy/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# the application package (torch-heavy modules are never imported by the service)
COPY money_agent ./money_agent

# To serve the trained model too, uncomment and provide a checkpoint:
# COPY checkpoint.pth ./checkpoint.pth

EXPOSE 8402

# waitress is a production WSGI server; build_app() returns the Flask app.
# Cloud hosts set $PORT; default to 8402 locally.
CMD ["sh", "-c", "waitress-serve --host=0.0.0.0 --port=${PORT:-8402} --call money_agent.service_x402:build_app"]
