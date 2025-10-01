from dataclasses import asdict
from backend.domain.types import LLMProvider, Validator


def test_validator_to_dict():
    validator = Validator(
        address="0x1234",
        stake=100,
        llmprovider=LLMProvider(
            provider="provider",
            model="model",
            config={"config": "config"},
            plugin="plugin",
            plugin_config={"plugin_config": "plugin_config"},
        ),
    )

    result = validator.to_dict()

    assert result == {
        "address": "0x1234",
        "stake": 100,
        "provider": "provider",
        "model": "model",
        "config": {"config": "config"},
        "plugin": "plugin",
        "plugin_config": {"plugin_config": "plugin_config"},
        "private_key": None,
    }
