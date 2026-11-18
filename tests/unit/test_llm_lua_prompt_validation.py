import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def lua_environment(tmp_path):
    lua = shutil.which("lua")
    if lua is None:
        pytest.skip("lua interpreter not available")

    (tmp_path / "lib-genvm.lua").write_text(
        textwrap.dedent(
            """
            local lib = {}

            function lib.log(_) end

            function lib.get_first_from_table(table)
              for key, value in pairs(table) do
                return { key = key, value = value }
              end
              return nil
            end

            lib.rs = {}

            function lib.rs.user_error(payload)
              error(
                payload.message
                .. "|" .. payload.causes[1]
                .. "|" .. tostring(payload.fatal),
                0
              )
            end

            function lib.rs.as_user_error(_) return nil end
            function lib.rs.json_stringify(_) return "{}" end
            function lib.rs.json_parse(_) return {} end

            return lib
            """
        )
    )
    (tmp_path / "lib-llm.lua").write_text(
        textwrap.dedent(
            """
            local llm = {}

            llm.providers = {
              ["provider-1"] = {
                models = {
                  ["model-1"] = {
                    use_max_completion_tokens = false,
                    meta = { config = {} },
                  },
                },
              },
            }
            llm.overloaded_statuses = {}
            llm.rs = {
              templates = {},
              exec_prompt_in_provider = function()
                error("provider should not be called", 0)
              end,
            }

            function llm.exec_prompt_transform(args)
              return {
                prompt = {
                  user_message = args.prompt,
                  images = {},
                  max_tokens = 0,
                  use_max_completion_tokens = false,
                },
                format = "text",
              }
            end

            return llm
            """
        )
    )

    return lua, tmp_path


def test_exec_prompt_rejects_blank_prompt_before_provider(lua_environment):
    lua, tmp_path = lua_environment
    test_script = tmp_path / "test_empty_prompt.lua"
    test_script.write_text(
        textwrap.dedent(
            f"""
            package.path = [[{tmp_path}/?.lua;]] .. package.path
            dofile([[{Path("backend/node/llm.lua").resolve()}]])

            local function assert_rejected(prompt)
              local ok, err = pcall(function()
                ExecPrompt({{ host_data = {{ studio_llm_id = "provider-1" }} }}, {{ prompt = prompt }}, 0)
              end)

              if ok then
                error("expected empty prompt to be rejected")
              end
              local err_str = tostring(err)
              if not string.find(err_str, "exec_prompt requires a non-empty prompt", 1, true) then
                error("missing empty prompt message: " .. err_str)
              end
              if not string.find(err_str, "EMPTY_PROMPT", 1, true) then
                error("missing EMPTY_PROMPT cause: " .. err_str)
              end
              if not string.find(err_str, "false", 1, true) then
                error("empty prompt should be non-fatal: " .. err_str)
              end
            end

            assert_rejected("")
            assert_rejected(" \\n\\t ")
            """
        )
    )

    subprocess.run(
        [lua, str(test_script)],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    "key,message,expected,provider_calls",
    [
        ("price (USD)", "Return the price (USD) today", "mocked", 0),
        ("100%", "Return 100% of the result", "mocked", 0),
        ("[result]", "Return [result] now", "mocked", 0),
        ("value.json", "Return valueXjson", "provider", 1),
        ("missing", "Unmatched prompt", "provider", 1),
        ("", "Any prompt", "mocked", 0),
        # Pattern matching backtracks explosively here; literal lookup is cheap.
        ("a*a*a*a*a*a*a*a*b", "a" * 100, "provider", 1),
    ],
)
def test_mock_keys_are_literal_substrings(
    lua_environment, key, message, expected, provider_calls
):
    lua, tmp_path = lua_environment
    test_script = tmp_path / "test_mock_matching.lua"
    test_script.write_text(
        textwrap.dedent(
            f"""
            package.path = [[{tmp_path}/?.lua;]] .. package.path
            dofile([[{Path("backend/node/llm.lua").resolve()}]])
            local llm = require("lib-llm")
            local calls = 0
            llm.rs.exec_prompt_in_provider = function()
              calls = calls + 1
              return {{ data = "provider" }}
            end
            local ctx = {{ host_data = {{
              studio_llm_id = "provider-1",
              mock_response = {{ response = {{ [ [=[{key}]=] ] = "mocked" }} }},
            }} }}
            local result = ExecPrompt(ctx, {{ prompt = [=[{message}]=] }}, 0)
            assert(result.data == "{expected}", "unexpected response: " .. result.data)
            assert(calls == {provider_calls}, "unexpected provider calls: " .. calls)
            assert(result.consumed_gen == 0)

            -- A specific literal match still takes priority over the default.
            ctx.host_data.mock_response.response = {{
              [""] = "default",
              ["price (USD)"] = "specific",
            }}
            result = ExecPrompt(ctx, {{ prompt = "Return price (USD)" }}, 0)
            assert(result.data == "specific")
            result = ExecPrompt(ctx, {{ prompt = "Something else" }}, 0)
            assert(result.data == "default")
            assert(calls == {provider_calls})
            """
        )
    )
    subprocess.run(
        [lua, str(test_script)],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )
