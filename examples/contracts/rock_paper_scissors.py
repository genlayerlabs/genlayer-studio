# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
from genlayer import *
import json

class RockPaperScissors(gl.Contract):
    last_result: str
    player_wins: u256
    ai_wins: u256
    ties: u256

    def __init__(self):
        self.last_result = "No game played yet"
        self.player_wins = 0
        self.ai_wins = 0
        self.ties = 0

    @gl.public.write
    def play(self, player_move: str) -> None:
        player_move = player_move.lower().strip()
        if player_move not in ["rock", "paper", "scissors"]:
            return

        prompt = """
You are playing Rock Paper Scissors.
Pick one move from: rock, paper, scissors.

Respond using ONLY the following format:
{
"move": str
}
It is mandatory that you respond only using the JSON format above,
nothing else. Don't include any other words or characters,
your output must be only JSON without any formatting prefix or suffix.
This result should be perfectly parseable by a JSON parser without errors.
"""

        def nondet():
            res = gl.nondet.exec_prompt(prompt)
            backticks = "``" + "`"
            res = res.replace(backticks + "json", "").replace(backticks, "")
            dat = json.loads(res)
            return dat["move"].lower().strip()

        ai_move = gl.eq_principle.strict_eq(nondet)

        if ai_move not in ["rock", "paper", "scissors"]:
            ai_move = "rock"

        if player_move == ai_move:
            outcome = "tie"
            self.ties += 1
        elif (
            (player_move == "rock" and ai_move == "scissors") or
            (player_move == "scissors" and ai_move == "paper") or
            (player_move == "paper" and ai_move == "rock")
        ):
            outcome = "player"
            self.player_wins += 1
        else:
            outcome = "ai"
            self.ai_wins += 1

        self.last_result = (
            f"Player: {player_move} | AI: {ai_move} | "
            f"Winner: {outcome}"
        )

    @gl.public.view
    def get_last_result(self) -> str:
        return self.last_result

    @gl.public.view
    def get_score(self) -> str:
        return (
            f"Player: {self.player_wins} | "
            f"AI: {self.ai_wins} | "
            f"Ties: {self.ties}"
        )
