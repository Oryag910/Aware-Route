import os

# app.main imports app.routing.ors at module load time, which reads
# ORS_API_KEY from the environment immediately. Without a real .env
# file (e.g. in CI), that import fails before any test runs. setdefault
# keeps a real key intact if one is provided, and individual tests can
# still override it with monkeypatch.setenv.
os.environ.setdefault("ORS_API_KEY", "test-only-fake-ors-key")
