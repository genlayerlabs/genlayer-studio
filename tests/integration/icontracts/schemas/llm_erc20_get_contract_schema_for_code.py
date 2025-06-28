llm_erc20_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {"kwparams": {}, "params": [["total_supply", "int"]]},
        "methods": {
            "get_balance_of": {
                "kwparams": {},
                "params": [["address", "string"]],
                "readonly": True,
                "ret": "int",
            },
            "get_balances": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": {"$dict": "int"},
            },
            "transfer": {
                "kwparams": {},
                "params": [["amount", "int"], ["to_address", "string"]],
                "payable": False,
                "readonly": False,
                "ret": "null",
            },
        },
    },
}
