# NEO Meme Coins

**Device-first Solana meme coin risk intelligence.**

GitHub repository: [angelmalevbiz-debug/neomemecoins](https://github.com/angelmalevbiz-debug/neomemecoins).
Web app: [GitHub Pages](https://angelmalevbiz-debug.github.io/neomemecoins/).
Publishing and backend setup: [GitHub Pages deployment](docs/GITHUB_PAGES.md).

NEO Meme Coins is being built as a browser-based scanner + Chrome/Chromium extension that turns raw market/on-chain data into understandable risk signals before a user considers a trade.

The architecture intentionally avoids owner-paid runtime APIs for the core scanner. Market data is fetched directly from public endpoints and Solana RPC calls run from the user's device. A user may optionally provide their own RPC endpoint, stored locally in the browser.

## Current alpha

The current `neo-meme-coins-v1` branch includes:

- deterministic risk engine with `SKIP / WAIT / WATCH / SETUP` posture
- live DEX Screener market data
- liquidity vs market-cap analysis
- absolute liquidity checks
- transaction imbalance and extreme volume/liquidity heuristics
- Solana holder concentration through RPC
- sampled fresh-wallet detection in the web app
- **device-side funding-source forensics for sampled top-holder wallets**
- **shared-funder cluster detection**
- **synchronized funding-time cluster detection**
- the same shared-funder checks inside the Chrome/Chromium extension
- confidence scoring that is reduced when public RPC evidence is incomplete
- local chart history with an early staircase-pattern heuristic
- deterministic narrative classification
- explicit data limitations and false-positive warnings
- device-local RPC settings
- Chrome Manifest V3 extension with a compact scanner popup
- DexScreener URL auto-detection in the extension
- static Vite web build published through GitHub Pages

## Important product rule

NEO is not designed as a magic BUY/SELL oracle. The engine separates:

1. **Observed data** — market, liquidity, transaction and holder data.
2. **Heuristics** — measurable but imperfect signals such as concentration, common funding or unusual flow.
3. **Confidence** — how complete the available evidence is.
4. **Posture** — whether the token should be skipped, watched, waited on, or allowed into a user's own trading setup.

A `SETUP` posture means the token passed the current checks well enough to evaluate against a strategy. It is not a guarantee of profit.

## Architecture

```text
User browser / extension
        |
        |-- DEX Screener public endpoints
        |     market cap, liquidity, volume, txns, pair age, socials
        |
        |-- Solana JSON-RPC
        |     supply, largest accounts, account owners
        |     wallet signature history + sampled transactions
        |
        |-- Local browser storage
        |     RPC preference, local price history, future journal/guardrails
        |
        `-- NEO deterministic analysis engine
              liquidity risk
              holder concentration
              fresh-wallet signals
              common-funder clusters
              synchronized funding-time clusters
              transaction imbalance
              chart heuristics
              narrative classification
              confidence + posture
```

The default public Solana RPC is useful for alpha/testing and can be rate-limited or have incomplete historical transaction coverage. The product therefore supports user-provided RPC endpoints without sending those settings to an NEO backend.

### Funding-forensics caveat

A common funding wallet is a coordination clue, not automatic proof of a bundle or scam. Centralized exchanges, bridges, faucets and shared funding services can make unrelated wallets appear linked. NEO surfaces the evidence and confidence instead of silently treating it as proof.

## Planned analysis modules

The next research/build phases include:

- deeper linked-wallet / bundle graph detection beyond first-funder evidence
- stronger fake-volume / fake-chart detection using richer historical samples
- tracker-wallet performance with consistency scoring, not one lucky trade
- richer narrative + catalyst analysis
- New Pairs / Final Stretch / Migrated filtering
- position-size and fee/slippage awareness
- trade journal and behavioral guardrails for revenge trading, overtrading and strategy drift
- historical validation dataset for rugs, bundles and successful launches
- optional user-controlled wallet actions only after explicit confirmation

Every heuristic must be validated against real historical tokens before becoming a strong rule.

## Web app

The PAPER backend audit, independent training books and causal replay are documented in
[PAPER runbook](docs/PAPER_RUNBOOK.md), [root-cause audit](docs/TRADING_ROOT_CAUSE.md),
[execution model](docs/EXECUTION_MODEL.md) and [strategy validation](docs/STRATEGY_VALIDATION.md).
Learning promotion is confined to separate PAPER portfolios; no live execution is enabled.

```bash
npm install
npm run dev
```

Production build:

```bash
npm run build
```

No Gemini key or NEO-owned paid API key is required by the current client-side scanner.

## Extension

For local Chromium testing:

1. Open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked**.
4. Select the repository's `extension/` folder.
5. Open a DexScreener Solana pair or paste a Solana token address into the popup.

A custom Solana RPC can be added from the extension settings. The URL is stored in Chrome local storage on the user's device.

## Project status

This is an **alpha research product**. Meme coins are extremely high-risk. Signals such as fresh wallets, raw top-token-account concentration, social presence, shared funding sources and chart shape are evidence to investigate, not standalone proof of fraud or future performance.
