# ideas for contract-to-contract interaction

- read / write
- run / deploy

## Chainlink like read contract

main "chainlink" oracle contract which reads data from the internet (e.g. which day is it today)

- this could use web requests + llms for parsing
- it could also use a public API

secondary consumer contracts would plug into the "chainlink" contract to consume it's data

## Uniswap

## Multiple Wizards passing the coin between them

## Multi sig

## Lottery

would require allowance
standard ERC20

## Proxy for upgrades

would require a sort of fallback method like python's `__getattr__`
