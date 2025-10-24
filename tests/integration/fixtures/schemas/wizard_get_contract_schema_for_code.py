wizard_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {"kwparams": {}, "params": [["have_coin", "bool"]]},
        "methods": {
            "ask_for_coin": {
                "kwparams": {},
                "params": [["request", "string"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
            "get_have_coin": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": "bool",
            },
        },
    },
}
