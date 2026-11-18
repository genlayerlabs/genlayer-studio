# Mock response performance (DXP-711)

Investigation baseline: `origin/main` at `c9407295`. The ticket contains no
reproduction, timings, or example configuration, so the original end-to-end
comparison cannot be reconstructed. The mock feature still exists; it is not
obsolete.

## Reproduced: mock lookup runs Lua patterns

`backend/node/llm.lua` describes mock keys as substrings but used
`string.find(message, key)`, which interprets keys as Lua patterns. This lookup
was introduced in the manager migration, commit `cbafc855` (PR #1352), and is
still present on the baseline.

- A key such as `price (USD)` does not match the literal text `price (USD)`.
  Without an empty-string default, the code falls through to the real provider,
  retaining network latency and potential provider fallback despite mocking.
- A key such as `100%` can raise a malformed-pattern error.
- A key such as `value.json` can incorrectly match `valueXjson`.
- A key such as `a*a*a*a*a*a*a*a*b` against 100 `a` characters spends more than
  five seconds backtracking in the local regression test. This is work that
  execution without mocks does not perform.

Use `string.find(message, key, 1, true)` to search literally. This keeps the
empty-string default and real-provider fallback for genuine misses. Callers
that relied on Lua pattern syntax must use literal prompt substrings instead.
The shared helper also handles equivalence-principle mock response maps.

Validation uses the real Lua entry point with stubbed provider calls, without
network requests. Before the fix, the pathological case hits the five-second
subprocess timeout; after the fix, the whole eight-test file passes in 0.05s
locally. This demonstrates lookup overhead, not an end-to-end Studio benchmark.

```sh
pytest tests/unit/test_llm_lua_prompt_validation.py -q
```

The tests require a `lua` executable (the existing test harness skips when it
is absent). They cover literal punctuation, false matches, genuine misses,
default precedence, provider-call counts, and bounded lookup completion.

## Additional setup overhead visible in the code

`tests/integration/icontracts/conftest.py::setup_validators` deletes the validator
pool and creates five validators individually for each mocked test, then deletes
those five individually at teardown. Each mutation goes through
`backend/validators/__init__.py::Manager.do_write`, which refreshes the snapshot
and stops/starts the LLM module. Mutations also publish validator-change events
to workers. Non-mocked setup reuses an existing pool when there are already five
validators. Thus suite wall time includes different setup costs; skipping LLM
inference alone does not guarantee a faster suite.

The pool reset prevents cross-test mock contamination (see the fixture's
comments); removing it would sacrifice correctness. Batching that lifecycle
requires separate RPC/fixture work and is not part of this lookup fix.

For an end-to-end comparison, measure fixture setup, transaction execution, and
teardown separately with the same contracts and validator count. Check the Lua
`no mock match found, falling through to real provider` log for unexpected
misses, and compare transaction execution timings separately from consensus
finalization and polling time. No live-provider benchmark was run for this fix.
