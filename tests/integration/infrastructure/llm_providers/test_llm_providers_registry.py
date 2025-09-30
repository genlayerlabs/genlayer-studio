from tests.common.request import payload, post_request_localhost
from tests.common.response import has_success_status

# provider, model, plugin


def test_llm_providers():
    providers_and_models_response = post_request_localhost(
        payload("sim_getProvidersAndModels")
    ).json()
    assert has_success_status(providers_and_models_response)
    providers_and_models = providers_and_models_response["result"]

    gpt4o_provider_id = next(
        (
            provider["id"]
            for provider in providers_and_models
            if provider["model"] == "gpt-4o"
            and provider["provider"] == "openai"
            and provider["plugin"] == "openai-compatible"
        ),
        None,
    )

    # Delete it
    response = post_request_localhost(
        payload("sim_deleteProvider", gpt4o_provider_id)
    ).json()
    assert has_success_status(response)

    # Create it again
    provider = {
        "provider": "openai",
        "model": "gpt-4o",
        "config": {},
        "plugin": "openai-compatible",
        "plugin_config": {"api_key_env_var": "OPENAIKEY", "api_url": None},
    }
    response = post_request_localhost(payload("sim_addProvider", provider)).json()
    assert has_success_status(response)

    provider_id = response["result"]

    updated_provider = {
        "provider": "openai",
        "model": "gpt-4o",
        "config": {},
        "plugin": "openai-compatible",
        "plugin_config": {"api_key_env_var": "OPENAIKEY", "api_url": None},
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
