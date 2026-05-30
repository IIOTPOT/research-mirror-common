# research-mirror-common

Shared library for the research-mirror Telegram bot-parsers: SQLite storage,
migrations, dedup, LLM summaries, hashtags, the MOEX equity ticker resolver,
health-integrity checks, and humane HTTP pacing.

Public so the source bots' CI can install it without a token. Contains **no
secrets or infrastructure** — only the importable `research_mirror_common`
package.

```
pip install git+https://github.com/IIOTPOT/research-mirror-common.git
```
