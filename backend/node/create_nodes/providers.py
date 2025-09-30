import json
import os
from threading import Thread
from typing import List
import socket

from jsonschema import Draft202012Validator, validate

from backend.domain.types import LLMProvider

current_directory = os.path.dirname(os.path.abspath(__file__))
schema_file = os.path.join(current_directory, "providers_schema.json")
default_providers_folder = os.path.join(current_directory, "default_providers")

default_providers_cache: List[LLMProvider] = []


def is_ollama_available() -> bool:
    """Check if Ollama service is available by attempting to connect to it."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("ollama", int(os.getenv("OLAMAPORT", "11434"))))
        sock.close()
        return result == 0
    except:
        return False


def get_schema() -> dict:
    with open(schema_file, "r") as f:
        schema = json.loads(f.read())

    Draft202012Validator.check_schema(schema)
    return schema


def validate_provider(provider: LLMProvider):
    # Convert to JSON
    provider_dict = provider.__dict__
    del provider_dict["id"]

    # Check against schema
    schema = get_schema()
    try:
        validate(instance=provider_dict, schema=schema)
    except Exception as e:
        raise ValueError(f"Error validating provider: {e}")


def _to_domain(provider: dict) -> LLMProvider:
    return LLMProvider(
        id=None,
        provider=provider["provider"],
        model=provider["model"],
        config=provider["config"],
        plugin=provider["plugin"],
        plugin_config=provider["plugin_config"],
    )


def get_default_providers() -> List[LLMProvider]:
    global default_providers_cache
    if default_providers_cache:
        return default_providers_cache

    schema = get_schema()

    files = [
        os.path.join(default_providers_folder, filename)
        for filename in os.listdir(default_providers_folder)
        if filename.endswith(".json")
    ]

    providers = []
    ollama_available = is_ollama_available()
    for file in files:
        with open(file, "r") as f:
            provider = json.loads(f.read())
        if ollama_available or provider["provider"] != "ollama":
            try:
                validate(instance=provider, schema=schema)
            except Exception as e:
                raise ValueError(
                    f"Error validating file {file}, provider {provider}: {e}"
                )

            providers.append(_to_domain(provider))

    default_providers_cache = providers
    return providers


# Start in another thread to avoid blocking the main thread
thread = Thread(target=get_default_providers, args=())
thread.start()


def get_default_provider_for(provider: str, model: str) -> LLMProvider:
    llm_providers = get_default_providers()
    matches = [
        llm_provider
        for llm_provider in llm_providers
        if llm_provider.provider == provider and llm_provider.model == model
    ]
    if not matches:
        raise ValueError(f"No default provider found for {provider} and {model}")
    if len(matches) > 1:
        raise ValueError(f"Multiple default providers found for {provider} and {model}")
    return matches[0]


# TODO: We could merge part of this logic of getting the available providers by loading the plugins. The plugins could have methods like `is_available` and `get_available_models` that would simplify this logic.
def create_random_providers(amount: int) -> list[LLMProvider]:
    """
    Not being used at the moment, left here for future reference.
    Creates random providers deriving them from the json schema.
    Internally uses hypothesis to generate the data, which is hacky since it's meant to be a testing library.
    """
    from hypothesis import HealthCheck, given, settings
    from hypothesis.errors import HypothesisDeprecationWarning
    from hypothesis_jsonschema import from_schema
    import warnings

    return_value = []

    with warnings.catch_warnings():  # Catch warnings from hypothesis telling us to not use it for this purpose
        warnings.simplefilter(
            "ignore", HypothesisDeprecationWarning
        )  # Disable the warning about using the deprecated `suppress_health_check` argument

        @settings(
            max_examples=amount, suppress_health_check=(HealthCheck.return_value,)
        )
        @given(
            from_schema(
                get_schema(),
            ),
        )
        def inner(value):
            nonlocal return_value  # Hypothesis `@given` wrapper doesn't allow us to return from the "test" function, so I'm using this closure to return the value
            provider = _to_domain(value)
            validate_provider(provider)
            return_value.append(provider)

    inner()  # Calling the function will fill the return_value list
    return return_value
