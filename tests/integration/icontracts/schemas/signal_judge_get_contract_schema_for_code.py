signal_judge_contract_schema = {
    "id": 1,
    "jsonrpc": "2.0",
    "result": {
        "ctor": {"kwparams": {}, "params": []},
        "methods": {
            "submit_signal": {
                "kwparams": {},
                "params": [
                    ["asset", "string"],
                    ["prediction", "string"],
                    ["reasoning", "string"],
                    ["target_price", "string"],
                    ["direction", "string"],
                    ["timeframe", "string"],
                ],
                "payable": False,
                "readonly": False,
                "ret": "any",
            },
            "resolve_signal": {
                "kwparams": {},
                "params": [["signal_id", "integer"]],
                "payable": False,
                "readonly": False,
                "ret": "any",
            },
            "get_signal_count": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": "integer",
            },
            "get_all_signals": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": "string",
            },
            "get_signals_by_asset": {
                "kwparams": {},
                "params": [["asset", "string"]],
                "readonly": True,
                "ret": "string",
            },
            "get_signals_by_status": {
                "kwparams": {},
                "params": [["status", "string"]],
                "readonly": True,
                "ret": "string",
            },
            "get_resolvable_signals": {
                "kwparams": {},
                "params": [],
                "readonly": True,
                "ret": "string",
            },
            "get_score": {
                "kwparams": {},
                "params": [["address", "string"]],
                "readonly": True,
                "ret": {"$dict": "any"},
            },
        },
    },
}