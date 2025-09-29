log_indexer_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {"kwparams": {}, "params": []},
        "methods": {
            "add_log": {
                "kwparams": {},
                "params": [["log", "string"], ["log_id", "int"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
            "get_closest_vector": {
                "kwparams": {},
                "params": [["text", "string"]],
                "readonly": True,
                "ret": {"$or": ["dict", "null"]},
            },
            "remove_log": {
                "kwparams": {},
                "params": [["id", "int"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
            "update_log": {
                "kwparams": {},
                "params": [["log_id", "int"], ["log", "string"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
        },
    },
}
