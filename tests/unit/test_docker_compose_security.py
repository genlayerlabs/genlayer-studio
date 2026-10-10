from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def test_postgres_is_bound_to_loopback_by_default():
    compose = yaml.safe_load(
        (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )

    assert compose["services"]["postgres"]["ports"] == [
        "127.0.0.1:${DBHOSTPORT:-5432}:5432"
    ]
