import os

from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status

provider_name = os.environ.get("TEST_PROVIDER", "openai")
model_name = os.environ.get("TEST_PROVIDER_MODEL", "gpt-4o")

API_KEY_MAP = {
    "openrouter": "OPENROUTERAPIKEY",
    "openai": "OPENAIKEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "google": "GEMINI_API_KEY",
}
api_key_env_var = API_KEY_MAP.get(provider_name, "OPENAIKEY")


def test_llm_providers():
    providers_and_models_response = post_request_localhost(
        payload("sim_getProvidersAndModels")
    ).json()
    assert has_success_status(providers_and_models_response)
    providers_and_models = providers_and_models_response["result"]

    target_provider_id = next(
        (
            provider["id"]
            for provider in providers_and_models
            if provider["model"] == model_name
            and provider["provider"] == provider_name
            and provider["plugin"] == "openai-compatible"
        ),
        None,
    )

    # Delete it
    response = post_request_localhost(
        payload("sim_deleteProvider", target_provider_id)
    ).json()
    assert has_success_status(response)

    # Create it again
    provider = {
        "provider": provider_name,
        "model": model_name,
        "config": {},
        "plugin": "openai-compatible",
        "plugin_config": {"api_key_env_var": api_key_env_var, "api_url": None},
    }
    response = post_request_localhost(payload("sim_addProvider", provider)).json()
    assert has_success_status(response)

    provider_id = response["result"]

    updated_provider = {
        "provider": provider_name,
        "model": model_name,
        "config": {},
        "plugin": "openai-compatible",
        "plugin_config": {"api_key_env_var": api_key_env_var, "api_url": None},
    }

    # Update it
    response = post_request_localhost(
        payload("sim_updateProvider", provider_id, updated_provider)
    ).json()
    assert has_success_status(response)

    # Reset it
    reset_result = post_request_localhost(
        payload("sim_resetDefaultsLlmProviders")
    ).json()
    assert has_success_status(reset_result)
