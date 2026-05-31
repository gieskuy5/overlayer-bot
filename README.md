# Overlayer Testnet Daily Bot

Multi-account automation for [Overlayer](https://testnet.overlayer.fi) testnet on Ethereum Sepolia.

## Features

- **Multi-account** — process multiple wallets sequentially
- **Per-account proxy** — each wallet uses its own proxy (or local)
- **HTTP fingerprint** — different User-Agent/headers per account
- **Auto-cooldown** — sleeps until next daily UTC reset after all accounts done
- **All on-chain tasks**: Mint, Stake, Bridge, Send, Receive, Daily 57 TX, OG Mint

## Setup

```bash
pip install web3 eth-account eth-abi requests
```

## Files

| File | Description |
|------|-------------|
| `bot.py` | Main script |
| `privkey.txt` | Private keys, one per line (`0x...`) |
| `proxy.txt` | Proxies, one per line (matched by index to privkey.txt) |

### privkey.txt
```
0xabc123...
0xdef456...
0x789ghi...
```

### proxy.txt
```
http://user:pass@host:port
socks5://user:pass@host:port

# empty line or # = skip (use local for that account)
```

If fewer proxies than keys, remaining accounts use local connection.

## Usage

```bash
python3 bot.py                 # run all tasks, all accounts
python3 bot.py --loop          # repeat every 24h with cooldown
python3 bot.py --daily         # only daily 57 tx
python3 bot.py --status        # check status only
python3 bot.py --account 0     # run single account by index
```

## Flow

1. Load wallets from `privkey.txt`
2. Assign proxy + fingerprint per account
3. For each account:
   - Authenticate via signed message (no browser)
   - Check pending tasks
   - Execute: Mint → Stake → Bridge → Send → Receive → Daily 57 TX → OG Mint
   - Wait 10-15s before next account
4. After all accounts: cooldown until next UTC midnight + 5 min

## Tasks & Points

| Task | Points |
|------|--------|
| Mint on Sepolia | 100 |
| Stake on Sepolia | 150 |
| Bridge from Sepolia | 150 |
| Send on Sepolia | 150 |
| Receive on Sepolia | 200 |
| Daily 57 Transactions | 2000 |
| OG Mint | — |

## Requirements

- Ethereum Sepolia ETH (for gas)
- Sepolia USDT + USDC (testnet tokens from faucet)
- Python 3.10+

## Disclaimer

For educational purposes on testnet only. Use at your own risk.
