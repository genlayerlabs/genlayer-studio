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
        Per-trader win/total counts are kept on-chain via TreeMap.
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
        """
        Submit a trading signal for LLM evaluation.

        Fetches the live price from Binance, asks the validator LLMs to judge
        whether the prediction is currently correct, and records the result
        on-chain. Uses prompt_comparative consensus so validators can agree on
        the boolean outcome even when their price fetches differ by cents.

        Args:
            asset:        Ticker symbol, e.g. BTC, ETH (alphanumeric only).
            prediction:   Human-readable prediction text.
            reasoning:    Trader's rationale for the prediction.
            target_price: Price target in USD as a string.
            direction:    ABOVE, BELOW, or AT — relative to target_price.
        """
        if direction not in ("ABOVE", "BELOW", "AT"):
            raise gl.vm.UserError("direction must be ABOVE, BELOW, or AT")

        asset_upper = asset.strip().upper()
        if not asset_upper or not prediction.strip():
            raise gl.vm.UserError("asset and prediction are required")
        if not asset_upper.isalnum():
            raise gl.vm.UserError("asset must be an alphanumeric ticker (e.g., BTC, ETH)")

        sender = gl.message.sender_address

        # Binance public ticker — no API key required
        price_url = (
            "https://api.binance.com/api/v3/ticker/price?symbol="
            + asset_upper
            + "USDT"
        )

        # rebind for the closure (all web fetch + LLM calls must run inside nondet block)
        _asset = asset_upper
        _prediction = prediction.strip()[:500]  # cap length to limit prompt-injection surface
        _reasoning = reasoning.strip()[:500]
        _target = target_price
        _direction = direction

        def get_judgment() -> str:
            """Fetch live price and ask LLM to evaluate the signal. Runs nondeterministically."""
            web_data = gl.nondet.web.render(price_url, mode="text")

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
            return result

        # prompt_comparative: validators agree on the boolean correct field;
        # current_price is allowed to differ by cents across validator fetches
        raw = gl.eq_principle.prompt_comparative(
            get_judgment,
            "The boolean field 'correct' must have the same value across all answers. Ignore any differences in 'current_price' (which varies by cents because it is fetched at different timestamps) and ignore differences in 'reasoning_quality' (which is a subjective rating).",
        )

        try:
            judgment = json.loads(raw)
        except json.JSONDecodeError as e:
            raise gl.vm.UserError(f"LLM did not return valid JSON: {e}")

        # defensive type coercion — some models return float for current_price
        # which breaks calldata encoding when stored
        judgment["current_price"] = str(judgment.get("current_price", ""))
        judgment["correct"] = bool(judgment.get("correct", False))
        judgment["reasoning_quality"] = int(judgment.get("reasoning_quality", 0))

        prev_wins = self.wins.get(sender, u256(0))
        prev_total = self.total.get(sender, u256(0))
        if judgment["correct"]:
            self.wins[sender] = u256(int(prev_wins) + 1)
        self.total[sender] = u256(int(prev_total) + 1)

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
        """Return the total number of signals submitted across all traders."""
        return len(json.loads(self.signals_json))

    @gl.public.view
    def get_all_signals(self) -> str:
        """Return all submitted signals as a JSON string."""
        return self.signals_json

    @gl.public.view
    def get_signals_by_asset(self, asset: str) -> str:
        """Return all signals for a given asset ticker as a JSON string."""
        signals = json.loads(self.signals_json)
        target = asset.strip().upper()
        return json.dumps([s for s in signals if s["asset"] == target])

    @gl.public.view
    def get_score(self, address: str) -> dict[str, typing.Any]:
        """
        Return win/total counts and win-rate percentage for a trader address.

        Win rate is returned as an integer percentage string to avoid storing floats.
        """
        try:
            addr = Address(address)
        except Exception as e:
            raise gl.vm.UserError(f"invalid address: {e}")
        w = int(self.wins.get(addr, u256(0)))
        t = int(self.total.get(addr, u256(0)))
        rate = "0" if t == 0 else str((w * 100) // t)
        return {"wins": w, "total": t, "win_rate_pct": rate}