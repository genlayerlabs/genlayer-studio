"""Classification of JSON-RPC methods for rate limiting.

Requests are split into two buckets:

- *cheap reads* — methods that never enter the GenVM and never touch an LLM.
  These are served from Postgres (or are outright constants), so the cost of
  serving one is orders of magnitude below a consensus round. They get their
  own, much larger bucket.
- *everything else* — the default. Keeps the pre-existing limits untouched.

The allowlist below is deliberately conservative and maintained by hand rather
than derived from any other list in the codebase. Two traps make that
necessary:

- ``DISABLE_INFO_LOGS_ENDPOINTS`` (set in the deployment env) looks like the
  natural source, but it contains ``eth_call`` — which runs contract code in
  the GenVM and can fan out to LLM validators. Reusing that list would make the
  single most expensive call in the system effectively free.
- ``gen_getContractSchema`` and ``gen_getContractSchemaForCode`` read like
  metadata lookups, but both build a ``Node`` backed by a ``GenVMManager`` to
  derive the schema from bytecode.

Being too conservative is cheap: an omitted method simply keeps today's limits.
Being too liberal hands out free capacity on a path that costs real money. When
in doubt, leave a method out.
"""

from __future__ import annotations

import json
from typing import Any

# Bodies larger than this are not parsed for classification — they are charged
# to the standard bucket. A cheap read is a handful of bytes; anything this
# large is a contract deployment or a batch, neither of which is cheap.
MAX_CLASSIFY_BODY_BYTES = 64 * 1024

CHEAP_READ_METHODS = frozenset(
    {
        # Constants / trivial responses
        "ping",
        "net_version",
        "eth_chainId",
        "eth_syncing",
        "eth_gasPrice",
        "eth_maxPriorityFeePerGas",
        "eth_blockNumber",
        "eth_feeHistory",
        "eth_getCode",  # returns a literal "0x"
        "eth_estimateGas",  # returns a constant
        "sim_getFinalityWindowTime",
        "sim_getConsensusContract",
        "sim_getFeeConfig",
        # Indexed database reads
        "eth_getBalance",
        "eth_getTransactionCount",
        "eth_getTransactionByHash",
        "eth_getTransactionReceipt",
        "eth_getBlockByHash",
        "eth_getBlockByNumber",
        "gen_getContractCode",
        "gen_getContractNonce",
        "gen_getTransactionStatus",
        "gen_getTransactionStatusDetails",
        "gen_getStudioTransactionByHash",
        "sim_getTransactionsForAddress",
    }
)

# Explicitly *not* cheap, recorded here so the reasoning survives future edits:
#   eth_call, gen_call, sim_call        -> execute contract code in the GenVM
#   gen_getContractSchema               -> builds a Node + GenVMManager
#   gen_getContractSchemaForCode        -> builds a Node + GenVMManager
#   sim_lintContract                    -> runs the GenVM linter
#   sim_estimateTransactionFees         -> executes the contract via sim_call to
#                                          measure fees. Note the contrast with
#                                          eth_estimateGas, which is allowlisted
#                                          because it returns a constant. The
#                                          names are near-identical; the cost is
#                                          not.
#   eth_getLogs                         -> unbounded range scan
#   eth_sendRawTransaction, sim_*, admin_*, dev_*  -> writes / privileged


def is_cheap_read_payload(raw_body: bytes) -> bool:
    """Return True if every call in this JSON-RPC body is a cheap read.

    Anything ambiguous — unparseable, oversized, empty, or a batch containing a
    single expensive call — is reported as *not* cheap, so uncertainty charges
    the stricter bucket rather than the looser one.
    """
    if not raw_body or len(raw_body) > MAX_CLASSIFY_BODY_BYTES:
        return False

    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError):
        return False

    if isinstance(payload, list):
        # A batch is only cheap if every member is. An empty batch is invalid
        # JSON-RPC and is charged normally.
        return bool(payload) and all(_is_cheap_call(call) for call in payload)

    return _is_cheap_call(payload)


def _is_cheap_call(call: Any) -> bool:
    if not isinstance(call, dict):
        return False
    method = call.get("method")
    return isinstance(method, str) and method in CHEAP_READ_METHODS
