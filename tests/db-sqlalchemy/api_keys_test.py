"""DB tests for ApiTier and ApiKey models."""

import hashlib

import pytest
from sqlalchemy.exc import IntegrityError

from backend.database_handler.models import ApiTier, ApiKey


class TestApiTier:
    def test_create_tier(self, session):
        tier = ApiTier(
            name="test", rate_limit_minute=10, rate_limit_hour=100, rate_limit_day=1000
        )
        session.add(tier)
        session.commit()

        result = session.query(ApiTier).filter_by(name="test").first()
        assert result is not None
        assert result.name == "test"
        assert result.rate_limit_minute == 10
        assert result.rate_limit_hour == 100
        assert result.rate_limit_day == 1000
        assert result.created_at is not None

    def test_unique_name_constraint(self, session):
        tier1 = ApiTier(
            name="free", rate_limit_minute=10, rate_limit_hour=100, rate_limit_day=1000
        )
        session.add(tier1)
        session.commit()

        tier2 = ApiTier(
            name="free", rate_limit_minute=20, rate_limit_hour=200, rate_limit_day=2000
        )
        session.add(tier2)
        with pytest.raises(IntegrityError):
            session.commit()


class TestApiKey:
    def _create_tier(self, session, name="free"):
        tier = ApiTier(
            name=name, rate_limit_minute=30, rate_limit_hour=500, rate_limit_day=5000
        )
        session.add(tier)
        session.commit()
        session.expire_all()
        return session.query(ApiTier).filter_by(name=name).first()

    def test_create_api_key(self, session):
        tier = self._create_tier(session)
        key_hash = hashlib.sha256(b"glk_test1234").hexdigest()
        api_key = ApiKey(
            key_prefix="glk_test",
            key_hash=key_hash,
            tier_id=tier.id,
            is_active=True,
        )
        session.add(api_key)
        session.commit()

        result = session.query(ApiKey).filter_by(key_hash=key_hash).first()
        assert result is not None
        assert result.key_prefix == "glk_test"
        assert result.is_active is True
        assert result.tier_id == tier.id

    def test_unique_key_hash_constraint(self, session):
        tier = self._create_tier(session)
        key_hash = hashlib.sha256(b"duplicate").hexdigest()
        k1 = ApiKey(key_prefix="glk_dup1", key_hash=key_hash, tier_id=tier.id)
        session.add(k1)
        session.commit()

        k2 = ApiKey(key_prefix="glk_dup2", key_hash=key_hash, tier_id=tier.id)
        session.add(k2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_foreign_key_enforcement(self, session):
        key_hash = hashlib.sha256(b"orphan").hexdigest()
        api_key = ApiKey(
            key_prefix="glk_orph",
            key_hash=key_hash,
            tier_id=99999,  # nonexistent tier
        )
        session.add(api_key)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_deactivate_api_key(self, session):
        tier = self._create_tier(session)
        key_hash = hashlib.sha256(b"deactivate").hexdigest()
        api_key = ApiKey(
            key_prefix="glk_deac",
            key_hash=key_hash,
            tier_id=tier.id,
            is_active=True,
        )
        session.add(api_key)
        session.commit()

        result = session.query(ApiKey).filter_by(key_hash=key_hash).first()
        result.is_active = False
        session.commit()

        session.expire_all()
        result = session.query(ApiKey).filter_by(key_hash=key_hash).first()
        assert result.is_active is False

    def test_description_is_optional(self, session):
        tier = self._create_tier(session)
        key_hash = hashlib.sha256(b"nodesc").hexdigest()
        api_key = ApiKey(
            key_prefix="glk_node",
            key_hash=key_hash,
            tier_id=tier.id,
        )
        session.add(api_key)
        session.commit()

        result = session.query(ApiKey).filter_by(key_hash=key_hash).first()
        assert result.description is None

    def test_description_with_value(self, session):
        tier = self._create_tier(session)
        key_hash = hashlib.sha256(b"withdesc").hexdigest()
        api_key = ApiKey(
            key_prefix="glk_with",
            key_hash=key_hash,
            tier_id=tier.id,
            description="Test API key",
        )
        session.add(api_key)
        session.commit()

        result = session.query(ApiKey).filter_by(key_hash=key_hash).first()
        assert result.description == "Test API key"
