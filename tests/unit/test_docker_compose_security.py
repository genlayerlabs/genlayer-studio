from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _compose_ports(service: str) -> list[str]:
    compose = yaml.safe_load(
        (REPOSITORY_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    )
    return compose["services"][service]["ports"]


def test_postgres_is_bound_to_loopback_by_default():
    assert _compose_ports("postgres") == ["127.0.0.1:${DBHOSTPORT:-5432}:5432"]


def test_redis_is_bound_to_loopback_by_default():
    assert _compose_ports("redis") == ["127.0.0.1:${REDISPORT:-6379}:6379"]


def test_ollama_is_bound_to_loopback_by_default():
    assert _compose_ports("ollama") == ["127.0.0.1:11434:11434"]
