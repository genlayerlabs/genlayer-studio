storage_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "abi": [
            {
                "inputs": [{"name": "initial_storage", "type": "string"}],
                "type": "constructor",
            },
            {
                "inputs": [],
                "name": "get_storage",
                "outputs": [{"name": "", "type": "string"}],
                "type": "function",
            },
            {
                "inputs": [{"name": "new_storage", "type": "string"}],
                "name": "update_storage",
                "outputs": [],
                "type": "function",
            },
        ],
        "class": "Storage",
    },
}
