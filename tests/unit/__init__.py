import os

os.environ.setdefault("TEST_WITH_MOCK_LLMS", "true")

from backend.env_loader import load_env

load_env()
