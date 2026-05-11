# invest-portfolio-bot

Personal Telegram bot. Reads your T-Invest portfolio, compares with `target.yaml`, and suggests what to buy to reduce drift. **Read-only — never places orders.**

## Quick start

### 1. BotFather

Create a bot in [@BotFather](https://t.me/BotFather), copy the token. Run `/setjoingroups` → `Disable`.

### 2. T-Invest token

Issue a **read-only** token in the T-Invest app: *Profile → Tokens → Issue*. Use the most restricted scope you can.

### 3. Find your Telegram numeric id

Open [@userinfobot](https://t.me/userinfobot), it replies with your `Id`. That's `OWNER_CHAT_ID`.

### 4. Find your account id

Run once with just `TINVEST_TOKEN` set to list accounts:

```python
import asyncio, os
from t_tech.invest import AsyncClient

async def main():
    async with AsyncClient(os.environ["TINVEST_TOKEN"]) as c:
        for a in (await c.users.get_accounts()).accounts:
            print(a.id, a.name, a.type, a.status)

asyncio.run(main())
```

Pick the account UUID you want to track. That's `TINVEST_ACCOUNT_ID`.

### 5. Configure

```bash
cp .env.example .env
$EDITOR .env          # fill BOT_TOKEN, TINVEST_TOKEN, OWNER_CHAT_ID, TINVEST_ACCOUNT_ID
$EDITOR target.yaml   # adjust to your target structure
```

### 6. Run locally

```bash
python -m venv .venv && source .venv/bin/activate

# t-tech-investments is published on T-Bank's own package index, not PyPI.
# Add it as an extra index for this install.
pip install --extra-index-url https://opensource.tbank.ru/api/v4/projects/238/packages/pypi/simple -e '.[dev]'

pytest -q              # math sanity check
python bot.py
```

In Telegram:

- `/start`, `/help` — intro
- `/portfolio` — current state vs target (drift per category and bucket)
- `/rebalance` — distribute current free cash across underweight buckets (keeps cash above `cash_target − 2 pp`)
- `/rebalance 50000` — distribute 50 000 ₽ of fresh money on top of the cash pool
- `/untracked` — positions outside of `target.yaml`

### 7. Deploy on VPS

```bash
git clone <repo> && cd invest-portfolio-bot
cp .env.example .env && $EDITOR .env
$EDITOR target.yaml
docker compose up -d --build
docker compose logs -f
```

Update:

```bash
git pull && docker compose up -d --build
```

## Target schema

The target is a flat list of **buckets**, grouped under **categories** for display only. Each bucket's `weight` is a percentage of the **whole portfolio**, and the sum across all buckets must equal 100. Categories themselves carry no weight — they're just labels.

A bucket binds value to instruments in one of three ways: explicit `tickers:`, a metadata `match:` filter, or `cash_currencies:` (route a non-base currency from T-Invest into this bucket). Exactly one bucket must be marked `is_cash: true` — the cash pool.

See [target.yaml](target.yaml) for a complete commented example. Sketch:

```yaml
base_currency: RUB

categories:
  stocks:
    buckets:
      sber:   { weight: 4, tickers: { SBER: 100 } }
      ru_etf: { weight: 35, tickers: { TMOS@: 100 } }

  bonds:
    buckets:
      ofz_short: { weight: 4, match: { bond_type: ofz, maturity_max_years: 3 } }
      ofz_long:  { weight: 5, match: { bond_type: ofz, maturity_min_years: 7 } }

  gold:
    buckets:
      gold:
        weight: 10
        cash_currencies: [XAU]      # XAU from T-Invest cash lands here, not in `cash`
        tickers: { GLDRUB_TOM: 100 }

  cash:
    buckets:
      liquid:
        weight: 5
        is_cash: true
        tickers: { TMON@: 100 }     # money-market fund counted as cash
```

Filter keys for `match:` (AND-ed when combined):

| key | values | notes |
|---|---|---|
| `bond_type` | `replaced` / `ofz` / `corp` / `any` | `replaced` = T-Invest `BOND_TYPE_REPLACED` (замещайки); `ofz` = class TQOB; `corp` = TQCB/TQTE/TQTD/TQIR |
| `nominal_currency` | `rub` / `usd` / `eur` / `not_rub` | currency of the bond's nominal |
| `class_code` | exact MOEX board code | e.g. `TQTF` for ETF |
| `maturity_max_years` | number | matches if remaining maturity ≤ N years |
| `maturity_min_years` | number | matches if remaining maturity > N years |

A filter-mode bucket always produces a **bucket allocation** in `/rebalance` (not a per-ticker buy) — bonds drift between buckets as maturity ticks down, so the bot reserves money for the category and lets you pick the instrument.

Hard invariants (bot crashes on violation):

- Sum of bucket weights across all categories = 100.
- Within a bucket, ticker weights sum to 100.
- Exactly one bucket has `is_cash: true`.
- An explicit ticker appears in at most one bucket.
- A `cash_currencies` entry appears in at most one bucket.
- Every explicit ticker resolves on a MOEX board at startup.

## Rebalance math

- `total_after = current_portfolio_value + extra_cash`
- For each non-cash bucket: `gap = total_after × portfolio_weight / 100 − current_value`. Positive gaps (underweight) get a pro-rata share of deployable cash; overweight buckets are left alone — **the bot never suggests sells**.
- **Cash floor**: cash bucket isn't depleted below `(cash_target − 2 pp)` of `total_after`. So with `cash.weight = 5`, deployment stops at 3% cash.
- **Drift cap +2 pp**: a buy that would push the destination bucket above `target + 2 pp` is dropped silently. Prevents single-lot overshoots in tiny buckets.
- **Explicit-ticker bucket** (e.g. `tickers: { SBER: 100 }`) → concrete `BuySuggestion` floored to whole lots. Leftover is then re-tried to promote any bucket that didn't fit a single lot from its pro-rata share.
- **Filter-mode bucket** (`match: { ... }`) → always a `BucketAllocation` (reserved amount with a hint of what to buy), never a per-ticker suggestion. Bonds drift between buckets as maturity ticks down, so the bot leaves the pick to you.
- **Filter group throttling**: within a `(category, bond_type)` group of ≥2 filter buckets, the smallest-gap one is dropped so deployable cash concentrates on the most underweight bucket of each kind.
- Unspent cash is reported as `Остаток`.

## Out of scope (MVP)

Historical snapshots · daily/weekly auto-reports · sell suggestions · multi-account aggregation · web UI · multi-user · drift alerts · inline keyboards · tax-aware logic.
