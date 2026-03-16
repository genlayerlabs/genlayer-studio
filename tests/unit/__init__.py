import os

os.environ.setdefault("TEST_WITH_MOCK_LLMS", "true")

from dotenv import load_dotenv

if not load_dotenv():
    load_dotenv(".env.example")
