FROM python:3.12-slim

WORKDIR /app

ENV TEST_PATH=tests/db-sqlalchemy

COPY ${TEST_PATH}/requirements.txt test-requirements.txt

# Migrations import from backend.* (e.g. backend.node.create_nodes.providers),
# so running `alembic upgrade head` needs the backend's own dependencies.
# Install both so the test environment matches what prd actually runs.
COPY backend/requirements.txt backend-requirements.txt

RUN pip install --no-cache-dir -r test-requirements.txt -r backend-requirements.txt

COPY ${TEST_PATH} .

COPY backend /app/backend

ENTRYPOINT ["pytest", "-svv"]
