# Rwanda Revenue Intelligence Agent

A Mistral Workflow that researches daily economic developments affecting tax
collection in Rwanda (RRA, MINECOFIN, NISR, BNR, RDB plus the local press),
drafts a one-page **Daily Revenue Intelligence Brief** in a fixed executive
format, and emails it every morning at 09:00 Kigali time (CAT, UTC+2).

## Workflow

`revenue-intelligence-brief` (`apps/worker/src/worker/workflows/revenue_brief.py`):

1. `brief-current-kigali-date` — current Kigali date (CAT, UTC+2).
2. `brief-collect-intelligence` — research: institutional sites and local press are fetched
   directly over HTTP (no Mistral web search / subscription needed), then summarized with a
   plain chat-completions call.
3. `brief-draft-brief` — structured draft into the fixed brief format (JSON schema-constrained).
4. `brief-send-email` — delivery over Gmail SMTP.

Input fields (`BriefInput`):

| Field       | Default        | Purpose                                    |
| ----------- | -------------- | ------------------------------------------ |
| `dry_run`   | `false`        | Return the rendered brief without emailing |
| `recipient` | from `.env`    | Override the destination address           |

## Required environment

| Variable              | Source                                                        |
| --------------------- | ------------------------------------------------------------- |
| `MISTRAL_API_KEY`     | Seeded from the Mistral platform for managed deployments      |
| `MISTRAL_MODEL`       | Optional model override (default: `mistral-small-latest`, free-tier safe) |
| `GMAIL_APP_PASSWORD`  | **Workspace secret** in Mistral Studio (Gmail app password)   |
| `GMAIL_FROM_USER`     | Plain env / Workspace secret — the sending Gmail address      |
| `BRIEF_RECIPIENT_EMAIL` | Plain env / Workspace secret — default recipient            |

Gmail app passwords require 2-Step Verification:
https://myaccount.google.com/apppasswords

## Local checks

```bash
cd apps/worker
uv run --all-packages pytest tests -q
```

From the app root:

```bash
bash tools/uv.sh tool run semgrep@1.176.0 --config .agents/skills/capability-workflows/scripts/linting/rules --metrics=off apps/worker/src/worker/workflows
bash tools/uv.sh tool run ruff@0.13.3 check apps/worker/src
```

## Deployment

### GitHub Actions (active scheduler)

`.github/workflows/daily-brief.yml` runs daily at 07:00 UTC (09:00 Kigali),
and can be triggered manually via **Run workflow** with an optional
`--dry-run` and recipient override.

Repository secrets required (Settings → Secrets and variables → Actions):

| Secret                | Value                          |
| --------------------- | ------------------------------ |
| `MISTRAL_API_KEY`     | Mistral API key                |
| `GMAIL_APP_PASSWORD`  | Gmail app password             |
| `GMAIL_FROM_USER`     | Optional: sending address      |
| `BRIEF_RECIPIENT_EMAIL` | Optional: recipient address  |

Only `MISTRAL_API_KEY` and `GMAIL_APP_PASSWORD` are mandatory; the addresses
default to the coded values.

### Managed deployment (optional)

The worker also deploys as a managed Mistral workflow deployment from this
repository (requires org quota); Mistral Studio schedules attach to it.
