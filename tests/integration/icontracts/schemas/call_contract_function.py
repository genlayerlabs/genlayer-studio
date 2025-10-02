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
                "gas_used": int,
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
}
