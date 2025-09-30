storage_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {"kwparams": {}, "params": [["initial_storage", "string"]]},
        "methods": {
            "get_storage": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": "string",
            },
            "update_storage": {
                "kwparams": {},
                "params": [["new_storage", "string"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
        },
    },
}
