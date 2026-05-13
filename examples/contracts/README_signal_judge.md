\# signal\_judge.py



Crypto trading signal evaluator. You submit a prediction (e.g. "BTC will stay above 100k this week"), the contract fetches the live price from Binance and asks the LLM validators to judge if you're right. Keeps a win/loss count per trader address.



Wrote this to explore the web fetch → LLM judgment → on-chain state pattern, which none of the other examples cover.



\## Usage



Deploy in GenLayer Studio with at least 3 validators configured. Call `submit\_signal` with:



\- `asset` — ticker, e.g. `BTC`, `ETH` (alphanumeric only)

\- `prediction` — what you think will happen

\- `reasoning` — why

\- `target\_price` — your price target as a string, e.g. `"100000"`

\- `direction` — `ABOVE`, `BELOW`, or `AT`



Returns a judgment dict with `correct` (bool), `current\_price`, and `reasoning\_quality` (1-10).



\## Read methods



\- `get\_all\_signals()` — returns JSON string, parse it

\- `get\_signals\_by\_asset("BTC")` — filtered

\- `get\_score("0x...")` — wins/total/win\_rate\_pct for an address

\- `get\_signal\_count()` — total submitted



\## A few things worth noting



Uses `prompt\_comparative` not `strict\_eq` — validators fetch the price at slightly different times so the price field drifts. Only the `correct` boolean needs to agree across validators.



Storage is all JSON strings — `list` and `dict` aren't supported as field types in this GenVM version. Leaderboard uses `TreeMap\[Address, u256]` for O(log n) lookups.



Companion UI: https://github.com/PratikshaGayen/signaljudge-ui

