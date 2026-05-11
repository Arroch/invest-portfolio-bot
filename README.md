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

- `/start` — intro
- `/portfolio` — current state vs target
- `/rebalance` — distribute current free cash across underweight positions
- `/rebalance 50000` — distribute 50 000 of fresh money

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

Each category uses one of two modes — **never both**:

**(A) `tickers:`** — explicit map of ticker → weight (% of category).

```yaml
stocks:
  weight: 60          # % of whole portfolio
  tickers:
    SBER: 70          # % within stocks
    YDEX: 30
```

**(B) `subcategories:`** — auto-buckets defined by `match:` filters. The bot reads instrument metadata and assigns each held instrument to the first matching subcategory (in YAML order). Explicit-ticker bindings always win over filter matches.

```yaml
bonds:
  weight: 30
  subcategories:
    replaced:
      weight: 40
      match: { bond_type: replaced }
    ofz_short:
      weight: 30
      match: { bond_type: ofz, maturity_max_years: 3 }
    ofz_mid:
      weight: 20
      match: { bond_type: ofz, maturity_max_years: 7 }
    ofz_long:
      weight: 10
      match: { bond_type: ofz }
```

Filter keys (AND-ed when combined):

| key | values | notes |
|---|---|---|
| `bond_type` | `replaced` / `ofz` / `corp` / `any` | `replaced` = T-Invest `BOND_TYPE_REPLACED` (замещайки); `ofz` = class TQOB; `corp` = TQCB/TQTE/TQTD/TQIR |
| `nominal_currency` | `rub` / `usd` / `eur` / `not_rub` | currency of the bond's nominal |
| `class_code` | exact MOEX board code | e.g. `TQTF` for ETF |
| `maturity_max_years` | number | matches if remaining maturity ≤ N years |
| `maturity_min_years` | number | matches if remaining maturity > N years |

A filter-mode bucket with no current holdings is allowed: it shows up in `/portfolio` as empty, and in `/rebalance` produces a warning ("add a matching ticker manually"). The bot never invents instruments to buy.

A full example using a real account is in [`target.example.yaml`](target.example.yaml).

Hard invariants (bot crashes on violation):

- Category weights sum to 100.
- Within a tickers-mode category, ticker weights sum to 100.
- Within a subcategories-mode category, subcategory weights sum to 100.
- Every explicit ticker resolves on a MOEX board at startup.
- An explicit ticker appears in at most one bucket.

Portfolio weight of a bucket = `category_weight × bucket_weight / 100`. So with `stocks.weight=60` and `SBER=70`, SBER targets `60 × 70 / 100 = 42%` of the whole portfolio.

## Rebalance math

- `total_after = current_portfolio_value + extra_cash`
- For each target **bucket**: `gap = total_after × portfolio_weight / 100 − current_value`
- Positive gaps (underweight) get pro-rata share of cash; overweight buckets are left alone — **the bot never suggests sells**.
- Within a multi-ticker bucket (filter mode), the bucket's allocation is split among held instruments **pro-rata to their current value** (so internal balance is preserved).
- An empty filter-mode bucket is reported separately as "empty underweight" — the bot can't pick a ticker for you.
- Allocations are floored to whole lots; the unspent remainder is reported as `Leftover`.

## Out of scope (MVP)

Historical snapshots · daily/weekly auto-reports · sell suggestions · multi-account aggregation · web UI · multi-user · drift alerts · inline keyboards · tax-aware logic.
