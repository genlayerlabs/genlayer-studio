# v0.2.16
# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
from datetime import datetime, timezone
import json
import typing


TIMEFRAMES = {
    "5min": 300,
    "15min": 900,
    "30min": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400,
    "test": 0,  # zero-second deadline for integration tests only
}


class SignalJudge(gl.Contract):
    signals_json: str
    wins: TreeMap[Address, u256]
    total: TreeMap[Address, u256]

    def __init__(self) -> None:
        """
        SignalJudge v2: a two-phase crypto prediction evaluator.

        Phase 1 (submit_signal): trader posts a prediction with a deadline.
        Stored as PENDING. No LLM call, cheap and fast.

        Phase 2 (resolve_signal): anyone can call after the deadline. The
        contract fetches the live price, asks validator LLMs whether the
        prediction held, and updates the trader's win/total counts.
        """
        self.signals_json = "[]"

    # ---------- helpers ----------

    def _load(self) -> list:
        return json.loads(self.signals_json)

    def _save(self, signals: list) -> None:
        self.signals_json = json.dumps(signals)

    def _now(self) -> int:
        # GenVM doesn't expose a block timestamp to contracts. The pattern used
        # by intelligent_oracle.py is to read datetime.now() directly. Validators
        # may differ by milliseconds, but they agree on the boolean "now >= deadline"
        # check as long as the deadline isn't seconds away. The shortest timeframe
        # of 5 minutes gives plenty of headroom for that consensus.
        return int(datetime.now(timezone.utc).timestamp())

    # ---------- phase 1: submit ----------

    @gl.public.write
    def submit_signal(
        self,
        asset: str,
        prediction: str,
        reasoning: str,
        target_price: str,
        direction: str,
        timeframe: str,
    ) -> dict[str, typing.Any]:
        """
        Submit a prediction to be judged at submission_time + duration_seconds.

        Args:
            asset:        Ticker — must be an alphanumeric string (e.g., BTC, ETH).
            prediction:   Human-readable prediction text.
            reasoning:    Trader's rationale.
            target_price: Price target in USD as a string.
            direction:    ABOVE, BELOW, or AT — relative to target_price.
            timeframe:    Must be one of: 5min, 15min, 30min, 1h, 4h, 1d.
        """
        asset_upper = asset.strip().upper()
        if not asset_upper.isalnum():
            raise gl.vm.UserError("asset must be an alphanumeric ticker (e.g., BTC, ETH)")
        if direction not in ("ABOVE", "BELOW", "AT"):
            raise gl.vm.UserError("direction must be ABOVE, BELOW, or AT")
        if not prediction.strip():
            raise gl.vm.UserError("prediction is required")
        if timeframe not in ("5min", "15min", "30min", "1h", "4h", "1d", "test"):
            raise gl.vm.UserError("timeframe must be one of: 5min, 15min, 30min, 1h, 4h, 1d")

        now = self._now()
        signals = self._load()
        signal_id = len(signals)

        signals.append(
            {
                "id": signal_id,
                "submitter": gl.message.sender_address.as_hex,
                "asset": asset_upper,
                "prediction": prediction.strip()[:500],
                "reasoning": reasoning.strip()[:500],
                "target_price": target_price,
                "direction": direction,
                "timeframe": timeframe,
                "deadline_ts": now + TIMEFRAMES[timeframe],
                "status": "PENDING",
                "current_price": "",
                "correct": False,
                "reasoning_quality": 0,
            }
        )
        self._save(signals)

        return {
            "signal_id": signal_id,
            "timeframe": timeframe,
            "deadline_ts": now + TIMEFRAMES[timeframe],
        }

    # ---------- phase 2: resolve ----------

    @gl.public.write
    def resolve_signal(self, signal_id: int) -> typing.Any:
        """
        Resolve a PENDING signal whose deadline has passed.

        Fetches the live price, asks validator LLMs to judge the prediction,
        updates leaderboard counts, marks the signal RESOLVED. Anyone can
        call this — there's no permission gate.
        """
        signals = self._load()
        if signal_id < 0 or signal_id >= len(signals):
            raise gl.vm.UserError(f"signal_id {signal_id} does not exist")

        sig = signals[signal_id]
        if sig["status"] != "PENDING":
            raise gl.vm.UserError(f"signal {signal_id} is already {sig['status']}")
        now = self._now()
        if now < sig["deadline_ts"]:
            raise gl.vm.UserError(
                f"deadline not reached yet (now={now}, deadline={sig['deadline_ts']})"
            )

        price_url = (
            "https://api.binance.com/api/v3/ticker/price?symbol="
            + sig["asset"]
            + "USDT"
        )

        # Rebind for the closure (nondet block can't capture self).
        _asset = sig["asset"]
        _prediction = sig["prediction"]
        _reasoning = sig["reasoning"]
        _target = sig["target_price"]
        _direction = sig["direction"]
        _timeframe = sig["timeframe"]

        def get_judgment() -> str:
            web_data = gl.nondet.web.render(price_url, mode="text")

            task = f"""
You are a crypto prediction evaluator. A trader submitted this prediction:

Asset: {_asset}
Prediction: "{_prediction}"
Direction: {_direction} (ABOVE = price above target, BELOW = below, AT = approximately equal)
Target price: ${_target} USD
Timeframe: {_timeframe} (this is a short-term prediction; judge whether the current price is on track for this prediction to be correct within the timeframe)
Reasoning: "{_reasoning}"

Live market data from Binance (current price at resolution time):
{web_data}

Extract the current price from the JSON above (the "price" field). Copy it
as a string, character for character — do not round.

Decide whether the prediction is correct GIVEN THE CURRENT PRICE and the
specified direction relative to the target.

Respond with ONLY this JSON, no markdown:
{{
    "correct": bool,
    "current_price": str,
    "reasoning_quality": int
}}

reasoning_quality is 1-10 based on soundness of the trader's reasoning.
Output must be parseable JSON, nothing else.
"""
            return gl.nondet.exec_prompt(task).replace("```json", "").replace("```", "")

        raw = gl.eq_principle.prompt_comparative(
            get_judgment,
            "The boolean field 'correct' must have the same value across all answers. "
            "Ignore any differences in 'current_price' (varies by cents because validators "
            "fetch at slightly different timestamps) and ignore 'reasoning_quality' "
            "(subjective rating).",
        )

        try:
            judgment = json.loads(raw)
        except json.JSONDecodeError as e:
            raise gl.vm.UserError(f"LLM did not return valid JSON: {e}")

        correct = bool(judgment.get("correct", False))
        current_price = str(judgment.get("current_price", ""))
        rq = int(judgment.get("reasoning_quality", 0))

        # Update leaderboard
        submitter = Address(sig["submitter"])
        prev_wins = self.wins.get(submitter, u256(0))
        prev_total = self.total.get(submitter, u256(0))
        if correct:
            self.wins[submitter] = u256(int(prev_wins) + 1)
        self.total[submitter] = u256(int(prev_total) + 1)

        # Update signal
        sig["status"] = "RESOLVED"
        sig["current_price"] = current_price
        sig["correct"] = correct
        sig["reasoning_quality"] = rq
        signals[signal_id] = sig
        self._save(signals)

        return {
            "signal_id": signal_id,
            "correct": correct,
            "current_price": current_price,
            "reasoning_quality": rq,
        }

    # ---------- views ----------

    @gl.public.view
    def get_signal_count(self) -> int:
        """Total signals submitted (any status)."""
        return len(self._load())

    @gl.public.view
    def get_all_signals(self) -> str:
        """All signals as JSON string."""
        return self.signals_json

    @gl.public.view
    def get_signals_by_status(self, status: str) -> str:
        """Filter signals by status: PENDING or RESOLVED."""
        s = status.strip().upper()
        return json.dumps([x for x in self._load() if x["status"] == s])

    @gl.public.view
    def get_signals_by_asset(self, asset: str) -> str:
        """Filter signals by asset ticker."""
        a = asset.strip().upper()
        return json.dumps([x for x in self._load() if x["asset"] == a])

    @gl.public.view
    def get_resolvable_signals(self) -> str:
        """PENDING signals whose deadline has passed (ready to resolve)."""
        now = self._now()
        ready = [
            x for x in self._load()
            if x["status"] == "PENDING" and now >= x["deadline_ts"]
        ]
        return json.dumps(ready)

    @gl.public.view
    def get_score(self, address: str) -> dict[str, typing.Any]:
        """Per-trader win/total/win-rate. Only resolved signals count."""
        try:
            addr = Address(address)
        except Exception as e:
            raise gl.vm.UserError(f"invalid address: {e}")
        w = int(self.wins.get(addr, u256(0)))
        t = int(self.total.get(addr, u256(0)))
        rate = "0" if t == 0 else str((w * 100) // t)
        return {"wins": w, "total": t, "win_rate_pct": rate}
