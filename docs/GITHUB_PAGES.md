# GitHub Pages publication

The maintained repository is https://github.com/angelmalevbiz-debug/neomemecoins.
The web app address is https://angelmalevbiz-debug.github.io/neomemecoins/.

`.github/workflows/deploy-pages.yml` runs the isolated PAPER regression suites,
checks TypeScript and the strategy lock, builds Vite, and uploads **only `dist/`**
through the official GitHub Pages artifact/deployment actions. Each push to
`main` publishes after these checks pass. `workflow_dispatch` allows a rebuild.
The relative Vite asset base supports the repository path.

In repository **Settings → Pages → Build and deployment**, select **GitHub
Actions** as the source. Publishing raw `main` with Jekyll does not compile React
or TypeScript and can expose source files instead of the built app. No custom
domain is needed for the GitHub address. Do not commit runtime account data,
private archives, credentials or recordings to publish a site.

GitHub Pages hosts the static browser application. It does not run the Python
market monitor, account gateway or learning worker. The web app currently uses
`https://neo-meme-api.169-58-211-177.sslip.io`; updating the frontend does not
deploy that service. Deploy the repaired backend separately with the existing
service manager and isolated account paths described in [PAPER_RUNBOOK.md](PAPER_RUNBOOK.md).
The gateway allows the new `https://angelmalevbiz-debug.github.io` origin;
the Supabase project's Auth redirect allowlist also needs the new app address
when OAuth is used. Keep existing redirect addresses during migration.

The imported repository did not contain the historical audit commit. To run
the frozen three-arm comparison from a fresh clone, first fetch the public
baseline without changing any branch or working file:

```sh
git fetch https://github.com/angelmalev9-creator/neo-meme-trade.git bccb0ed30bac1efbf2e16cfe89e0ee1af049f050
python scripts/compare_main.py --input tests/fixtures/paper_gap_v9.jsonl --output-dir .runtime/comparison
```

The baseline hash and exact pre-repair manifest remain documented in the audit.
Synthetic replay demonstrates correct accounting, not profitable market edge.
