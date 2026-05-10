# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json
import typing


class SignalJudge(gl.Contract):
    signals_json: str
    wins: TreeMap[Address, u256]
    total: TreeMap[Address, u256]

    def __init__(self) -> None:
        """
        SignalJudge: a crypto trading-signal evaluator.
        Traders submit a price prediction; the contract fetches the live price
        from Binance and asks an LLM to judge whether the prediction was correct.
        Per-trader win/total counts are kept on-chain.
        """
        self.signals_json = "[]"

    @gl.public.write
    def submit_signal(
        self,
        asset: str,
        prediction: str,
        reasoning: str,
        target_price: str,
        direction: str,
    ) -> typing.Any:
        if direction not in ("ABOVE", "BELOW", "AT"):
            raise gl.vm.UserError("direction must be ABOVE, BELOW, or AT")
        if asset.strip() == "" or prediction.strip() == "":
            raise gl.vm.UserError("asset and prediction are required")

        sender = gl.message.sender_address
        asset_upper = asset.upper()

        # Binance public ticker — no key, no auth
        price_url = (
            "https://api.binance.com/api/v3/ticker/price?symbol="
            + asset_upper
            + "USDT"
        )

        # rebind for the closure (web fetch + LLM must run inside nondet block)
        _asset = asset_upper
        _prediction = prediction
        _reasoning = reasoning
        _target = target_price
        _direction = direction

        def get_judgment() -> str:
            web_data = gl.nondet.web.render(price_url, mode="text")
            print(web_data)

            task = f"""
You are a crypto trading-signal evaluator. A trader submitted this prediction:

Asset: {_asset}
Prediction: "{_prediction}"
Direction: {_direction} (ABOVE = current price above target, BELOW = below, AT = approximately equal)
Target price: ${_target} USD
Reasoning: "{_reasoning}"

Live market data from Binance:
{web_data}

Extract the current price from the JSON above (the "price" field). Copy it as a string,
character for character — do not round, do not reformat.

Decide whether the trader's signal is currently correct given the direction and target.

Respond with ONLY this JSON, no markdown, no extra text:
{{
    "correct": bool,
    "current_price": str,
    "reasoning_quality": int
}}

reasoning_quality is 1-10 based on how sound the trader's reasoning is.
Output must be parseable JSON, nothing else.
"""
            result = gl.nondet.exec_prompt(task).replace("```json", "").replace("```", "")
            print(result)
            return result

        # prompt_comparative — validators agree on the *judgment* (correct field)
        # even if current_price differs by cents between fetches at different timestamps
        raw = gl.eq_principle.prompt_comparative(
            get_judgment,
            "The boolean field 'correct' must have the same value across all answers. Ignore any differences in 'current_price' (which varies by cents because it is fetched at different timestamps) and ignore differences in 'reasoning_quality' (which is a subjective rating).",
        )
        judgment = json.loads(raw)

        # defensive type coercion (some models return float for current_price,
        # which breaks calldata encoding when stored)
        judgment["current_price"] = str(judgment.get("current_price", ""))
        judgment["correct"] = bool(judgment.get("correct", False))
        judgment["reasoning_quality"] = int(judgment.get("reasoning_quality", 0))

        # update per-trader counts
        prev_wins = self.wins.get(sender, u256(0))
        prev_total = self.total.get(sender, u256(0))
        if judgment["correct"]:
            self.wins[sender] = u256(int(prev_wins) + 1)
        self.total[sender] = u256(int(prev_total) + 1)

        # append to the signal log
        signals = json.loads(self.signals_json)
        signals.append(
            {
                "submitter": sender.as_hex,
                "asset": _asset,
                "prediction": _prediction,
                "target_price": _target,
                "direction": _direction,
                "current_price": judgment["current_price"],
                "correct": judgment["correct"],
                "reasoning_quality": judgment["reasoning_quality"],
            }
        )
        self.signals_json = json.dumps(signals)

        return judgment

    @gl.public.view
    def get_signal_count(self) -> int:
        return len(json.loads(self.signals_json))

    @gl.public.view
    def get_all_signals(self) -> str:
        return self.signals_json

    @gl.public.view
    def get_signals_by_asset(self, asset: str) -> str:
        signals = json.loads(self.signals_json)
        target = asset.upper()
        return json.dumps([s for s in signals if s["asset"] == target])

    @gl.public.view
    def get_score(self, address: str) -> dict[str, typing.Any]:
        addr = Address(address)
        w = int(self.wins.get(addr, u256(0)))
        t = int(self.total.get(addr, u256(0)))
        # win-rate as a percentage string so we never store/return a float
        rate = "0" if t == 0 else str((w * 100) // t)
        return {"wins": w, "total": t, "win_rate_pct": rate}