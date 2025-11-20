"""Schema definitions for call_contract_function API responses."""

from typing import Optional

call_contract_function_response = {
    "consensus_data": {
        "leader_receipt": [
            {
                "result": dict,
                "calldata": dict,
                "eq_outputs": dict,
                "execution_result": str,
                "genvm_result": dict,
                "mode": str,
                "node_config": {
                    "address": str,
                    "private_key": str,
                    "stake": int,
                    "primary_model": {
                        "config": dict,
                        "model": str,
                        "provider": str,
                        "plugin": str,
                        "plugin_config": dict,
                        "fallback_validator": Optional[str],
                    },
                    "secondary_model": Optional[dict],
                },
                "vote": Optional[str],
            }
        ],
        "validators": list,
        "votes": dict,
    },
    "created_at": str,
    "data": {
        "calldata": dict,
    },
    "from_address": str,
    "hash": str,
    "status": int,
    "status_name": str,
    "to_address": str,
    "type": int,
    "value": int,
    "result": int,
    "gaslimit": int,
    "nonce": int,
    "leader_only": bool,
    "contract_snapshot": dict,
    "appeal_validators_timeout": bool,
    "sim_config": dict,
    "sender": str,
    "recipient": str,
    "tx_id": str,
    "activator": str,
    "last_leader": str,
    "result_name": str,
    "num_of_rounds": str,
    "last_round": dict,
}
