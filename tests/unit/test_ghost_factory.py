import pytest

from backend.protocol_rpc.ghost_factory import (
    GhostFactoryConfig,
    InvalidGhostFactoryConfiguration,
)


def test_default_virtual_factory_matches_frozen_create_vectors(monkeypatch):
    for name in (
        "GENLAYER_STUDIO_GHOST_FACTORY_ADDRESS",
        "GENLAYER_STUDIO_CREATION_PHASE_ADDRESS",
        "GENLAYER_STUDIO_GHOST_BYTECODE_HASH",
        "GENLAYER_STUDIO_GHOST_FACTORY_INITIAL_NONCE",
    ):
        monkeypatch.delenv(name, raising=False)

    factory = GhostFactoryConfig.from_env()
    assert factory.address_for(0, 0) == "0x0aD72A9a303bDF888d3bf7d76e3568248a353199"
    assert factory.address_for(0, 1) == "0xC6a967E0b0D12c109CD6367F245B422Ae05565A4"
    assert (
        factory.address_for(
            42,
            0,
            namespace="0x1111111111111111111111111111111111111111",
        )
        == "0x25A58acd32f777db380EA378cCE191972aa62c5e"
    )


def test_create2_address_ignores_factory_nonce_but_create_does_not():
    factory = GhostFactoryConfig.from_env()
    namespace = "0x1111111111111111111111111111111111111111"
    assert factory.address_for(42, 0, namespace=namespace) == factory.address_for(
        42,
        99,
        namespace=namespace,
    )
    assert factory.address_for(0, 0) != factory.address_for(0, 1)


def test_create2_salt_is_scoped_to_authenticated_sender():
    factory = GhostFactoryConfig.from_env()
    first = factory.address_for(
        42,
        0,
        namespace="0x1111111111111111111111111111111111111111",
    )
    second = factory.address_for(
        42,
        0,
        namespace="0x2222222222222222222222222222222222222222",
    )

    assert first != second


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GENLAYER_STUDIO_GHOST_FACTORY_ADDRESS", "not-an-address"),
        ("GENLAYER_STUDIO_CREATION_PHASE_ADDRESS", "0x12"),
        ("GENLAYER_STUDIO_GHOST_BYTECODE_HASH", "0x1234"),
        ("GENLAYER_STUDIO_GHOST_FACTORY_INITIAL_NONCE", "-1"),
    ],
)
def test_virtual_factory_rejects_invalid_configuration(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(InvalidGhostFactoryConfiguration):
        GhostFactoryConfig.from_env()


def test_virtual_factory_rejects_non_uint256_salt():
    factory = GhostFactoryConfig.from_env()
    with pytest.raises(InvalidGhostFactoryConfiguration):
        factory.address_for(-1, 0)
    with pytest.raises(InvalidGhostFactoryConfiguration):
        factory.address_for(1 << 256, 0)
    with pytest.raises(InvalidGhostFactoryConfiguration):
        factory.address_for(1, 0)
