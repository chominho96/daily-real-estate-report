# daily-real-estate-report

Daily real-estate market report pipeline with fixed report format and section-wise content generation.

## Core Design

- The report structure is fixed.
- Each section body is generated independently (LLM hook enabled).
- Regions are fully configurable from `config/regions.yaml` (no code edits).
- Real-estate data is collected by this repository's own API client.
- Scheduled by GitHub Actions and published as a mobile-friendly static site with MkDocs + GitHub Pages.

## Fixed Report Sections (v1)

1. Policy news between last run and current run
2. Price trend by region and asset (apartment/villa/officetel)
3. Additional insights

## Project Structure

```text
.
├── .github/workflows/daily-report.yml
├── config/
│   ├── regions.yaml
│   └── report.yaml
├── data/
│   ├── processed/
│   └── raw/
├── docs/
│   ├── index.md
│   └── reports/
├── state/last_run.json
├── src/daily_report/
│   ├── analysis/metrics.py
│   ├── collectors/
│   │   ├── news.py
│   │   └── real_estate.py
│   ├── llm/section_writer.py
│   ├── render/markdown.py
│   ├── main.py
│   ├── models.py
│   ├── pipeline.py
│   └── settings.py
└── mkdocs.yml
```

## Quick Start

```bash
python -m venv .venv
source .venv/bin/activate
pip install .[dev]
```

Run once locally:

```bash
daily-report --root .
```

Generated report path is printed to stdout.

## Region Management

Edit only `config/regions.yaml`:

- `enabled: true/false` to include/exclude region
- `assets` to control apartment/villa/officetel per region
- Optional fallback: `config/regions.json` and `config/report.json` are used when PyYAML is unavailable.

## Public API Integration (Own Implementation)

Set direct API client settings in `config/report.yaml`:

```yaml
real_estate_api:
  enabled: true
  base_url: "https://apis.data.go.kr"
  service_key_env: "MOLIT_API_SERVICE_KEY"
  endpoint_by_asset:
    apartment: "<endpoint path or full url>"
    villa: "<endpoint path or full url>"
    officetel: "<endpoint path or full url>"
```

Runtime behavior:

- Uses `serviceKey`, `LAWD_CD`, `DEAL_YMD`, `numOfRows`, `pageNo` style parameters.
- Parses XML `<item>` records and aggregates daily average deal price + transaction count.
- If API settings are missing or request fails, synthetic sample data is used automatically.

## News Source (Naver)

Configure in `config/report.yaml`:

```yaml
news:
  provider: "naver"
  naver_client_id_env: "NAVER_NEWS_CLIENT_ID"
  naver_client_secret_env: "NAVER_NEWS_CLIENT_SECRET"
  naver_sort: "date"
```

Runtime behavior:

- Uses Naver News Search OpenAPI first.
- If Naver credentials are missing or request fails, falls back to Google News RSS.

Reference note:

- `real-estate-mcp` should be used only as a reference for endpoint/parameter patterns.
- This project does not call `real-estate-mcp` at runtime.

## LLM Section Generation

This project uses Codex CLI (`codex exec`) for section generation.

Local prerequisite:

```bash
npm install -g @openai/codex
```

Set `CODEX_CLI_KEY` to the full `auth.json` payload from a ChatGPT-authenticated Codex CLI session for non-interactive environments (e.g. GitHub Actions).

- Without valid Codex auth, deterministic fallback section text is used.
- With valid Codex auth, each section prompt is generated independently and injected into fixed report template through Codex CLI.

## GitHub Actions + Pages

Workflow: `.github/workflows/daily-report.yml`

- Runs daily at `22:00 UTC` (`07:00 KST`).
- Generates report, commits artifacts, builds MkDocs site, deploys to GitHub Pages.

Required repo setup:

1. Add repository secret: `CODEX_CLI_KEY` (auth.json payload from Codex CLI login).
2. Add repository secret: `MOLIT_API_SERVICE_KEY` (required if `real_estate_api.enabled=true`).
3. Add repository secret: `NAVER_NEWS_CLIENT_ID` (required for Naver news source).
4. Add repository secret: `NAVER_NEWS_CLIENT_SECRET` (required for Naver news source).
5. Enable GitHub Pages with "GitHub Actions" source.
