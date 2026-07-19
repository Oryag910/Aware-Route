# Must match the Python version that built the committed graph pickle
# (see data/manhattan_walk_graph.v1.manifest.json -> python_version), or the
# graph fails to unpickle at runtime and the app falls back to ORS.
FROM python:3.14-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY data/ data/

CMD uvicorn app.main:app --host 0.0.0.0 --port $PORT
