#!/usr/bin/env python3
"""
Overlayer Testnet Multi-Account Bot
- Multi-wallet with per-account proxy + fingerprint
- Sequential processing: account 1 → 10-15s delay → account 2 → ...
- Auto-cooldown until next daily reset

Usage:
  python3 bot.py                 # run all tasks, all accounts
  python3 bot.py --loop          # repeat every 24h
  python3 bot.py --daily         # only 57 daily tx
  python3 bot.py --status        # check only
  python3 bot.py --account 0     # run single account by index

Files:
  privkey.txt    — one private key per line (0x...)
  proxy.txt      — one proxy per line (http://user:pass@host:port)
"""

import os, sys, json, time, traceback, random, requests
from datetime import datetime, timezone, timedelta
from eth_account import Account
from eth_account.messages import encode_defunct
from web3 import Web3
from web3.exceptions import TimeExhausted
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.stdout.reconfigure(line_buffering=True)
sys.stderr.reconfigure(line_buffering=True)

# ─── Constants ───
BASE_API = "https://api.overlayer.fi"
RPC_LIST = [
    "https://ethereum-sepolia-rpc.publicnode.com",
    "https://rpc.sepolia.org",
    "https://ethereum-sepolia-rpc.publicnode.com",
]
CHAIN_ID = 11155111

USDT = "0xaA8E23Fb1079EA71e0a56F48a2aA51851D8433D0"
USDC = "0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8"
T_PLUS = "0xe20534a32f9162488a90026F268a74fBE28d272D"
C_PLUS = "0xE815718D44694ec4637CB775C468d87f6e15B538"
STAKED_C = "0x753937137Eb92871A6F3517514d4f1Ee860e3FDF"
LZ_EID_BASE = 40245

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROGRESS_FILE = os.path.join(SCRIPT_DIR, "progress.json")
PRIVKEY_FILE = os.path.join(SCRIPT_DIR, "privkey.txt")
PROXY_FILE = os.path.join(SCRIPT_DIR, "proxy.txt")

# ─── ABIs ───
ERC20_ABI = [
    {"constant":True,"inputs":[{"name":"_owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":True,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"_spender","type":"address"},{"name":"_amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"_owner","type":"address"},{"name":"_spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"_to","type":"address"},{"name":"_value","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"type":"function"},
]
MINT_ABI = [
    {"inputs":[{"components":[{"internalType":"address","name":"benefactor","type":"address"},{"internalType":"address","name":"beneficiary","type":"address"},{"internalType":"address","name":"collateral","type":"address"},{"internalType":"uint256","name":"collateralAmount","type":"uint256"},{"internalType":"uint256","name":"overlayerWrapAmount","type":"uint256"}],"internalType":"struct MintRedeemManagerTypes.Order","name":"order","type":"tuple"}],"name":"mint","outputs":[],"stateMutability":"nonpayable","type":"function"}
]
VAULT_ABI = [
    {"inputs":[{"internalType":"uint256","name":"assets","type":"uint256"},{"internalType":"address","name":"receiver","type":"address"}],"name":"deposit","outputs":[{"internalType":"uint256","name":"","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
]
OFT_ABI = [
    {"inputs":[{"components":[{"internalType":"uint32","name":"dstEid","type":"uint32"},{"internalType":"bytes32","name":"to","type":"bytes32"},{"internalType":"uint256","name":"amountLD","type":"uint256"},{"internalType":"uint256","name":"minAmountLD","type":"uint256"},{"internalType":"bytes","name":"extraOptions","type":"bytes"},{"internalType":"bytes","name":"composeMsg","type":"bytes"},{"internalType":"bytes","name":"oftCmd","type":"bytes"}],"internalType":"struct SendParam","name":"_sendParam","type":"tuple"},{"components":[{"internalType":"uint256","name":"nativeFee","type":"uint256"},{"internalType":"uint256","name":"lzTokenFee","type":"uint256"}],"internalType":"struct MessagingFee","name":"_fee","type":"tuple"},{"internalType":"address","name":"_refundAddress","type":"address"}],"name":"send","outputs":[{"components":[{"internalType":"bytes32","name":"guid","type":"bytes32"},{"internalType":"uint64","name":"nonce","type":"uint64"},{"components":[{"internalType":"uint256","name":"nativeFee","type":"uint256"},{"internalType":"uint256","name":"lzTokenFee","type":"uint256"}],"internalType":"struct MessagingFee","name":"fee","type":"tuple"}],"internalType":"struct MessagingReceipt","name":"","type":"tuple"},{"components":[{"internalType":"uint256","name":"amountSentLD","type":"uint256"},{"internalType":"uint256","name":"amountReceivedLD","type":"uint256"}],"internalType":"struct OFTReceipt","name":"","type":"tuple"}],"stateMutability":"payable","type":"function"},
    {"inputs":[],"name":"approvalRequired","outputs":[{"internalType":"bool","name":"","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"components":[{"internalType":"uint32","name":"dstEid","type":"uint32"},{"internalType":"bytes32","name":"to","type":"bytes32"},{"internalType":"uint256","name":"amountLD","type":"uint256"},{"internalType":"uint256","name":"minAmountLD","type":"uint256"},{"internalType":"bytes","name":"extraOptions","type":"bytes"},{"internalType":"bytes","name":"composeMsg","type":"bytes"},{"internalType":"bytes","name":"oftCmd","type":"bytes"}],"internalType":"struct SendParam","name":"_sendParam","type":"tuple"},{"internalType":"bool","name":"_payInLzToken","type":"bool"}],"name":"quoteSend","outputs":[{"components":[{"internalType":"uint256","name":"nativeFee","type":"uint256"},{"internalType":"uint256","name":"lzTokenFee","type":"uint256"}],"internalType":"struct MessagingFee","name":"","type":"tuple"}],"stateMutability":"view","type":"function"},
]

# ─── Fingerprints ───
FINGERPRINTS = [
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*", "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'},
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9,fr;q=0.8", "Accept": "application/json, text/plain, */*", "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*", "Sec-Ch-Ua": '"Google Chrome";v="125", "Chromium";v="125", "Not.A/Brand";v="24"'},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36", "Accept-Language": "en-US,en;q=0.9,de;q=0.8", "Accept": "application/json, text/plain, */*"},
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:126.0) Gecko/20100101 Firefox/126.0", "Accept-Language": "en-US,en;q=0.5", "Accept": "application/json, text/plain, */*"},
    {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15", "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*"},
    {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0", "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*"},
    {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:126.0) Gecko/20100101 Firefox/126.0", "Accept-Language": "en-US,en;q=0.5,fr;q=0.3", "Accept": "application/json, text/plain, */*"},
]

# ─── Helpers ───
def log(msg, account_idx=None):
    prefix = f"[Acct {account_idx}] " if account_idx is not None else ""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {prefix}{msg}", flush=True)

def load_json(path, default=None):
    try:
        with open(path) as f: return json.load(f)
    except: return default or {}

def save_json(path, data):
    with open(path, "w") as f: json.dump(data, f, indent=2)

def load_lines(path):
    try:
        with open(path) as f:
            return [l.strip() for l in f if l.strip() and not l.startswith("#")]
    except: return []

def select_rpc():
    for url in RPC_LIST:
        try:
            w3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 5}))
            if w3.is_connected():
                w3.eth.block_number
                return url
        except: continue
    return RPC_LIST[0]

# ─── Account Context ───
class AccountCtx:
    def __init__(self, idx, wallet, proxy=None):
        self.idx = idx
        self.addr = Web3.to_checksum_address(wallet["address"])
        self.pk = wallet["private_key"]
        if not self.pk.startswith("0x"):
            self.pk = "0x" + self.pk
        self.account = Account.from_key(self.pk)
        self.proxy = proxy

        # HTTP session with fingerprint + proxy
        fp = FINGERPRINTS[idx % len(FINGERPRINTS)]
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json", **fp})

        retry = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
        self.session.mount("https://", HTTPAdapter(max_retries=retry))
        self.session.mount("http://", HTTPAdapter(max_retries=retry))

        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

        # Web3 with same proxy
        rpc = select_rpc()
        if proxy:
            from web3 import HTTPProvider
            provider = HTTPProvider(rpc, session=self.session, request_kwargs={"timeout": 30})
            self.w3 = Web3(provider)
        else:
            self.w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 30}))

        self.today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def log(self, msg):
        log(msg, self.idx)

    def get_gas_price(self):
        """Gas price bumped 1.3x for faster Sepolia confirmation."""
        return int(self.w3.eth.gas_price * 1.3)

    def get_nonce(self):
        return self.w3.eth.get_transaction_count(self.addr, 'pending')

    def send_tx(self, tx_dict, label="tx"):
        signed = self.w3.eth.account.sign_transaction(tx_dict, self.pk)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        self.log(f"  {label}: {tx_hash.hex()[:20]}...")
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
        except TimeExhausted:
            # Replace-by-fee: resubmit same nonce with higher gas
            try:
                nonce = tx_dict.get('nonce') or tx_dict['nonce']
                bump_tx = dict(tx_dict)
                bump_tx['gasPrice'] = int(self.get_gas_price() * 1.5)
                bump_tx['nonce'] = nonce
                signed2 = self.w3.eth.account.sign_transaction(bump_tx, self.pk)
                tx_hash2 = self.w3.eth.send_raw_transaction(signed2.raw_transaction)
                self.log(f"  {label}: RBF bump → {tx_hash2.hex()[:20]}...")
                receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash2, timeout=300)
                ok = receipt.status == 1
                self.log(f"  {label}: {'OK' if ok else 'REVERTED'} (gas: {receipt.gasUsed})")
                return receipt
            except:
                self.log(f"  {label}: TIMEOUT (may still confirm)")
                return None
        ok = receipt.status == 1
        self.log(f"  {label}: {'OK' if ok else 'REVERTED'} (gas: {receipt.gasUsed})")
        return receipt

    def build_tx(self, fn, extra=None):
        params = {'from': self.addr, 'nonce': self.get_nonce(), 'gasPrice': self.get_gas_price(), 'chainId': CHAIN_ID}
        if extra: params.update(extra)
        try:
            est = fn.estimate_gas(params)
            params['gas'] = int(est * 1.3)
        except Exception as e:
            self.log(f"  Gas est fail: {str(e)[:60]}, using 300k")
            params['gas'] = 300000
        return fn.build_transaction(params)

    def erc20(self, token_addr):
        return self.w3.eth.contract(address=Web3.to_checksum_address(token_addr), abi=ERC20_ABI)

    def get_balance(self, token_addr):
        c = self.erc20(token_addr)
        try: return c.functions.balanceOf(self.addr).call(), c.functions.decimals().call()
        except: return 0, 18

    def approve_if_needed(self, token_addr, spender, amount):
        c = self.erc20(token_addr)
        try: cur = c.functions.allowance(self.addr, Web3.to_checksum_address(spender)).call()
        except: cur = 0
        if cur >= amount: return True
        self.log(f"  Approving {token_addr[:12]}...")
        tx = self.build_tx(c.functions.approve(Web3.to_checksum_address(spender), 2**256 - 1))
        r = self.send_tx(tx, "approve")
        return r and r.status == 1

    # ─── Auth ───
    def get_jwt(self):
        self.session.post(f"{BASE_API}/api-s/gdpr-consent/{self.addr}", json={})
        r = self.session.get(f"{BASE_API}/api-s/auth/nonce/{self.addr}")
        data = r.json()
        if not data.get("success"): return None
        exp = int(time.time()) + 300
        msg = f"Request Overlayer social session\n{self.addr}\n{exp}\n{data['nonce']}"
        sig = "0x" + self.account.sign_message(encode_defunct(text=msg)).signature.hex()
        r = self.session.post(f"{BASE_API}/api-s/auth/verify/{self.addr}", json={"message": msg, "signature": sig})
        d = r.json()
        return d.get("token") if d.get("success") else None

    def api_get(self, path, token, params=None):
        return self.session.get(f"{BASE_API}{path}", params=params, headers={"Authorization": f"Bearer {token}"}).json()

    # ─── Tasks ───
    def mint_tokens(self, amount, collateral_addr, wrapped_addr, label):
        self.log(f"Minting {label}: {amount / 1e6:.2f} tokens...")
        if not self.approve_if_needed(collateral_addr, wrapped_addr, amount):
            self.log(f"  Approve failed for {label}"); return False
        c = self.w3.eth.contract(address=Web3.to_checksum_address(wrapped_addr), abi=MINT_ABI)
        order = (self.addr, self.addr, Web3.to_checksum_address(collateral_addr), amount, amount * 10**12)
        tx = self.build_tx(c.functions.mint(order))
        r = self.send_tx(tx, f"mint {label}")
        return r and r.status == 1

    def stake_tokens(self, token_addr, vault_addr, amount, label):
        self.log(f"Staking {amount / 1e18:.4f} {label}...")
        if not self.approve_if_needed(token_addr, vault_addr, amount): return False
        v = self.w3.eth.contract(address=Web3.to_checksum_address(vault_addr), abi=VAULT_ABI)
        tx = self.build_tx(v.functions.deposit(amount, Web3.to_checksum_address(self.addr)))
        r = self.send_tx(tx, f"stake {label}")
        return r and r.status == 1

    def bridge_c_plus(self, amount):
        """Bridge C+ to Base Sepolia via OFT contract object."""
        self.log(f"Bridging {amount / 1e18:.4f} C+ to Base Sepolia...")
        if not self.approve_if_needed(C_PLUS, C_PLUS, amount):
            return False

        oft = self.w3.eth.contract(address=Web3.to_checksum_address(C_PLUS), abi=OFT_ABI)
        to_b32 = b'\x00' * 12 + bytes.fromhex(self.addr[2:])
        send_param = (LZ_EID_BASE, to_b32, amount, amount, b'', b'', b'')

        # quoteSend via contract
        try:
            fee = oft.functions.quoteSend(send_param, False).call()
            nf = fee[0]
            self.log(f"  LZ fee: {self.w3.from_wei(nf, 'ether'):.6f} ETH")
        except Exception as e:
            self.log(f"  quoteSend err: {e}")
            return False

        # send via contract
        try:
            fn = oft.functions.send(send_param, (nf, 0), Web3.to_checksum_address(self.addr))
            params = fn.build_transaction({
                'from': self.addr,
                'nonce': self.get_nonce(),
                'gasPrice': self.get_gas_price(),
                'value': nf,
                'chainId': CHAIN_ID,
            })
        except Exception as e:
            self.log(f"  send build err: {e}")
            return False

        try:
            params['gas'] = int(self.w3.eth.estimate_gas(params) * 1.3)
        except:
            params['gas'] = 300000
        r = self.send_tx(params, "bridge")
        return r and r.status == 1

    def send_t_plus(self, amount):
        self.log(f"Sending {amount / 1e18:.4f} T+...")
        c = self.erc20(T_PLUS)
        tx = self.build_tx(c.functions.transfer(Web3.to_checksum_address(self.addr), amount))
        r = self.send_tx(tx, "send T+")
        return r and r.status == 1

    def receive_task(self):
        self.log("Receive: self-transfer 10 T+...")
        c = self.erc20(T_PLUS)
        tx = self.build_tx(c.functions.transfer(Web3.to_checksum_address(self.addr), 10 * 10**18))
        r = self.send_tx(tx, "receive T+")
        return r and r.status == 1

    def daily_mint_loop(self, count=57):
        self.log(f"Daily TX loop: {count} mints...")
        usdt_bal, _ = self.get_balance(USDT)
        per_tx = max(100000, usdt_bal // (count + 10))
        ok = 0
        for i in range(count):
            self.log(f"  TX {i+1}/{count}")
            try:
                if self.mint_tokens(per_tx, USDT, T_PLUS, "T+"): ok += 1
                time.sleep(0.5)
            except Exception as e:
                self.log(f"  Err: {str(e)[:80]}"); time.sleep(2)
        self.log(f"Daily: {ok}/{count} OK")
        return ok

    def mint_og(self):
        try:
            r = self.session.get(f"{BASE_API}/api-s/auth/nonce/{self.addr}")
            d = r.json()
            if not d.get("success"): return
            exp = int(time.time()) + 300
            msg = f"Request Overlayer OG mint\n{self.addr}\n{exp}\n{d['nonce']}"
            sig = "0x" + self.account.sign_message(encode_defunct(text=msg)).signature.hex()
            r = self.session.post(f"{BASE_API}/api-s/auth/verify/{self.addr}", json={"message": msg, "signature": sig})
            tk = r.json().get("token")
            if not tk: return
            r = self.session.get(f"{BASE_API}/api-s/socials/og/mint/nonce/{self.addr}", headers={"Authorization": f"Bearer {tk}"})
            nd = r.json()
            if not nd.get("success"): return
            msg2 = f"Request Overlayer OG mint\n{self.addr}\n{exp}\n{nd['nonce']}"
            sig2 = "0x" + self.account.sign_message(encode_defunct(text=msg2)).signature.hex()
            r = self.session.post(f"{BASE_API}/api-s/socials/og/mint/{self.addr}", json={"message": msg2, "signature": sig2}, headers={"Authorization": f"Bearer {tk}"})
            rd = r.json()
            if rd.get("success"): self.log(f"OG minted! ID: {rd.get('tokenId')}")
            elif "already" in str(rd).lower(): self.log("OG already minted")
            else: self.log(f"OG: {rd.get('error', str(rd)[:100])}")
        except Exception as e: self.log(f"OG err: {e}")

    # ─── Status ───
    def print_status(self, token=None):
        eth = self.w3.from_wei(self.w3.eth.get_balance(self.addr), "ether")
        usdt, _ = self.get_balance(USDT)
        usdc, _ = self.get_balance(USDC)
        tp, _ = self.get_balance(T_PLUS)
        cp, _ = self.get_balance(C_PLUS)
        proxy_info = f"proxy={self.proxy[:30]}..." if self.proxy else "proxy=local"
        self.log(f"[{self.addr[:10]}...] ETH:{eth:.5f} USDT:{usdt/1e6:.1f} USDC:{usdc/1e6:.1f} T+:{tp/1e18:.1f} C+:{cp/1e18:.1f} {proxy_info}")

        if not token: token = self.get_jwt()
        if not token: self.log("Auth FAIL"); return token, None

        tasks = self.api_get("/api-s/socials/onchain-tasks", token, {"address": self.addr}).get("tasks", [])
        pending = [t for t in tasks if not t.get("completed")]
        self.log(f"Tasks: {len(tasks)-len(pending)}/{len(tasks)} done")
        for t in tasks:
            self.log(f"  {'OK' if t.get('completed') else '..'} {t['title']} ({t['points']}pts)")
        try:
            pts = self.api_get(f"/api-s/socials/onchain-tasks/points/{self.addr}", token)
            self.log(f"Points: {pts.get('totalPoints', 0)}")
        except: pass
        return token, tasks

    # ─── Run ───
    def run_all(self):
        self.log("=" * 50)
        self.log("Overlayer DAILY BOT")
        self.log("=" * 50)

        token, tasks = self.print_status()
        if not token or not tasks: return

        pending = [t for t in tasks if not t.get("completed")]
        if not pending:
            self.log("All tasks done!"); return

        self.log(f"--- EXECUTING {len(pending)} TASKS ---")
        usdt_bal, _ = self.get_balance(USDT)
        usdc_bal, _ = self.get_balance(USDC)
        cp_bal, _ = self.get_balance(C_PLUS)

        need_c = any('bridge' in t['title'].lower() or 'stake' in t['title'].lower() for t in pending)
        if need_c and cp_bal < 600 * 10**18 and usdc_bal >= 700_000_000:
            try:
                self.mint_tokens(700_000_000, USDC, C_PLUS, "C+")
                time.sleep(2); cp_bal, _ = self.get_balance(C_PLUS)
            except Exception as e: self.log(f"Mint C+ err: {e}")

        if any('mint' in t['title'].lower() for t in pending) and usdt_bal >= 200_000_000:
            try: self.mint_tokens(200_000_000, USDT, T_PLUS, "T+")
            except Exception as e: self.log(f"Mint T+ err: {e}")
            time.sleep(2)

        if any('stake' in t['title'].lower() for t in pending):
            cp_bal, _ = self.get_balance(C_PLUS)
            if cp_bal >= 500 * 10**18:
                try: self.stake_tokens(C_PLUS, STAKED_C, 500 * 10**18, "C+")
                except Exception as e: self.log(f"Stake err: {e}")
            else: self.log(f"C+ low for stake: {cp_bal/1e18:.1f}")
            time.sleep(2)

        if any('bridge' in t['title'].lower() for t in pending):
            cp_bal, _ = self.get_balance(C_PLUS)
            if cp_bal >= 300 * 10**18:
                try: self.bridge_c_plus(300 * 10**18)
                except Exception as e: self.log(f"Bridge err: {e}")
            else: self.log(f"C+ low for bridge: {cp_bal/1e18:.1f}")
            time.sleep(2)

        if any(t['title'].lower().startswith('send') for t in pending):
            tp_bal, _ = self.get_balance(T_PLUS)
            if tp_bal >= 150 * 10**18:
                try: self.send_t_plus(150 * 10**18)
                except Exception as e: self.log(f"Send err: {e}")
            else: self.log(f"T+ low for send: {tp_bal/1e18:.1f}")
            time.sleep(2)

        if any('receive' in t['title'].lower() for t in pending):
            try: self.receive_task()
            except Exception as e: self.log(f"Receive err: {e}")
            time.sleep(2)

        if any('daily' in t['title'].lower() or 'transaction' in t['title'].lower() for t in pending):
            try: self.daily_mint_loop(57)
            except Exception as e: self.log(f"Daily err: {e}")

        self.log("--- OG ---")
        self.mint_og()

        self.log("=" * 50)
        self.print_status()


# ─── Main ───
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Overlayer Multi-Account Bot")
    parser.add_argument("--loop", action="store_true", help="Repeat every 24h")
    parser.add_argument("--daily", action="store_true", help="Only daily 57 tx")
    parser.add_argument("--status", action="store_true", help="Check only")
    parser.add_argument("--account", type=int, default=-1, help="Run single account by index")
    args = parser.parse_args()

    # Load wallets from privkey.txt (one private key per line)
    raw_keys = load_lines(PRIVKEY_FILE)
    if not raw_keys:
        log(f"NO KEYS FOUND: {PRIVKEY_FILE}")
        log("Create privkey.txt with one private key per line (0x...)")
        sys.exit(1)
    wallets = []
    for pk in raw_keys:
        pk = pk.strip()
        if not pk.startswith("0x"): pk = "0x" + pk
        try:
            addr = Account.from_key(pk).address
            wallets.append({"address": addr, "private_key": pk})
        except Exception as e:
            log(f"Invalid key: {pk[:10]}... ({e})")
    log(f"Loaded {len(wallets)} wallets")

    # Load proxies
    proxies = load_lines(PROXY_FILE)
    log(f"Loaded {len(proxies)} proxies")

    # Build contexts
    accounts = []
    for i, w in enumerate(wallets):
        proxy = proxies[i] if i < len(proxies) else None
        accounts.append(AccountCtx(i, w, proxy))

    if args.account >= 0:
        if args.account < len(accounts):
            accounts = [accounts[args.account]]
        else:
            log(f"Account {args.account} not found (max: {len(wallets)-1})")
            sys.exit(1)

    while True:
        log("=" * 60)
        log(f"RUNNING {len(accounts)} ACCOUNTS")
        log("=" * 60)

        for i, ctx in enumerate(accounts):
            try:
                if args.status:
                    ctx.print_status()
                elif args.daily:
                    ctx.daily_mint_loop(57)
                else:
                    ctx.run_all()
            except Exception as e:
                log(f"FATAL: {e}", ctx.idx)
                traceback.print_exc()

            # Delay between accounts
            if i < len(accounts) - 1:
                delay = random.randint(10, 15)
                log(f"Waiting {delay}s before next account...")
                time.sleep(delay)

        if not args.loop:
            break

        # Cooldown until next UTC midnight + 5 min
        now = datetime.now(timezone.utc)
        next_run = (now + timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
        wait_secs = int((next_run - now).total_seconds())
        log(f"\nAll accounts done. Cooldown until {next_run.strftime('%Y-%m-%d %H:%M UTC')} ({wait_secs//3600}h {(wait_secs%3600)//60}m)")
        time.sleep(wait_secs)


if __name__ == "__main__":
    main()
