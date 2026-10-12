# CivicAtlas

An observable Maryland public-data application by Pauline Yeary: React, TypeScript, Vite, Bun, Cloudflare Workers/D1, Python and DuckDB. Production: https://atlas.goldenmonkey.dev. Portfolio: https://goldenmonkey.dev/work/civicatlas/.

## Launch status — October 11, 2026

The application is live against Cloudflare D1. Production verification checked all 24 county responses and all 504 source observations against the validated snapshot: 530 requests and 9,736 assertions, with no mismatch. GitHub Actions validation, automatic deployment and the separately checked snapshot import have passed against production. The import saved a recovery export before updating D1 and verified the exact deployed revision afterward. Analytics Engine capture has been verified for real conference-page, comparison and source-inspector interactions using a sampling-aware query. Verification traffic is not recruiter engagement.

## The snapshot

2024 BLS QCEW annual data for all 24 Maryland jurisdictions, private ownership code 5. Employment measures covered workplace jobs. Average pay is nominal annual pay per job, not household income. NAICS sectors divide by the same county private-sector total. BLS disclosure code N is withheld; a missing observation is unavailable; a disclosed zero stays zero.

`data/raw/manifest.json` pins 24 official BLS county CSV files, official industry titles and the Census 2024 1:5 million county geometry by URL, retrieval time and SHA-256. The offline pipeline validates those hashes and produces byte-reproducible SQL, Parquet, GeoJSON and snapshot JSON. Source inspection connects displayed values to the original source fields. Baltimore city 24510 and Baltimore County 24005 are separate jurisdictions.

## Run and verify

Use Python 3.12, uv and Bun 1.3.4. The raw public snapshot is included; routine builds require no source API key or network fetch.

```sh
uv sync --frozen
bun install --frozen-lockfile
uv run python -m pipeline.build --check
uv run python -m unittest discover -s tests/python -v
uv run python tools/sentinel.py --root . --entry pipeline.build:main --required pipeline.validate:validate_snapshot
bun run check
bun run db:local
bun run dev
```

The Cloudflare Vite plugin serves the frontend and Worker together at localhost:5173. Local D1 is seeded separately. `bun run build` emits the production client and Worker. Worker tests execute the actual seed schema using SQLite; production smoke checks separately establish Cloudflare/D1 connectivity.

## API and failure behavior

GET /api/v1/counties returns all county totals; GET /api/v1/counties/{fips} returns a county and its NAICS sectors; GET /api/v1/sources/{id} returns provenance; GET /api/health checks D1 and the data revision. Identifiers stay strings, queries are parameterized, public SQL submission is unavailable, and API faults return explicit JSON errors. The UI provides retry and retains ordinary portfolio/contact navigation.

## Bounded Sentinel

The fresh AST checker verifies supported local imported function references and static reachability to the required production validator. It does not prove branch execution, runtime behavior or general correctness. Dynamic constructs it cannot resolve are reported unresolved. The malformed-reference, disconnected-validator, test-only-reference, valid and dynamic fixtures make these boundaries reproducible. The recording and transcript are in data/evidence; fixture and production commands are shown without private source or identifiers.

## Deploy and import

```sh
bun run deploy
```

Main-branch GitHub Actions validates before deployment. Configure `CLOUDFLARE_API_TOKEN` in the production environment with Workers Scripts Edit, Workers Routes Edit, Account Settings Read, Account Analytics Read and Zone Read scoped to the configured account and goldenmonkey.dev zone. Deploy does not import or replace data. The manually dispatched import-snapshot workflow checks the pinned artifacts, exports a recovery backup and imports data with a separate `CLOUDFLARE_D1_TOKEN` (D1 Edit). Ordinary deployments retain the snapshot.

To roll back code, use `bunx wrangler deployments list` and `bunx wrangler rollback <version-id>`. A code rollback does not restore D1; keep the exported snapshot and use D1 Time Travel or the verified backup for data recovery.

## Privacy and engagement

POST /api/events accepts only event, surface and campaign labels, no extra fields. Accepted events: conference_viewed, comparison_used, source_opened, github_clicked, resume_downloaded, walkthrough_started and email_clicked. No persistent visitor identifier, fingerprint, email or contact record is stored. A public no-transform response prevents automatic beacon injection; the manual page beacon and application events honor browser privacy signals. Browser GPC/DNT prevents analytics collection; telemetry faults never block exploration. Cloudflare still processes ordinary connection metadata to deliver the application. These aggregate actions cannot identify recruiters or prove a linked visitor funnel; an email click measures intent.

Private Analytics Engine report (Account Analytics Read token required):

```sql
SELECT blob1 AS event, blob2 AS surface, SUM(_sample_interval) AS actions
FROM goldenmonkey_engagement
WHERE timestamp >= NOW() - INTERVAL '7' DAY AND blob3 = 'nava-2026'
GROUP BY event, surface ORDER BY actions DESC FORMAT JSON
```

New code is MIT licensed; fonts keep their SIL Open Font Licenses. Official public data is attributed in its manifest and source inspector.
