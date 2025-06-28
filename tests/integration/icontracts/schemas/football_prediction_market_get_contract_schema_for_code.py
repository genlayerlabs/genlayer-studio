football_prediction_market_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {
            "kwparams": {},
            "params": [
                ["game_date", "string"],
                ["team1", "string"],
                ["team2", "string"],
            ],
        },
        "methods": {
            "get_resolution_data": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": {"$dict": "any"},
            },
            "resolve": {
                "kwparams": {},
                "params": [],
                "payable": False,
                "readonly": False,
                "ret": "any",
            },
        },
    },
}
