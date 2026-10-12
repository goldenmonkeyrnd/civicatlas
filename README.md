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
uv sync --locked
bun install --frozen-lockfile
uv run --locked python -m pipeline.build --check
uv run --locked python -m unittest discover -s tests/python -v
uv run --locked sentinels check --root . --config sentinels.toml --json
bun run check
bun run db:local
bun run dev
```

The Cloudflare Vite plugin serves the frontend and Worker together at localhost:5173. Local D1 is seeded separately. `bun run build` emits the production client and Worker. Worker tests execute the actual seed schema using SQLite; production smoke checks separately establish Cloudflare/D1 connectivity.

## API and failure behavior

GET /api/v1/counties returns all county totals; GET /api/v1/counties/{fips} returns a county and its NAICS sectors; GET /api/v1/sources/{id} returns provenance; GET /api/health checks D1 and the data revision. Identifiers stay strings, queries are parameterized, public SQL submission is unavailable, and API faults return explicit JSON errors. The UI provides retry and retains ordinary portfolio/contact navigation.

## Released Sentinels contracts

The pipeline consumes [Sentinels v0.1.0](https://github.com/goldenmonkeyrnd/sentinels/releases/tag/v0.1.0) through an exact GitHub wheel URL and SHA-256 in `pyproject.toml`, with its dependency closure recorded in `uv.lock`. `uv sync --locked` verifies that the manifest and lock agree. Sentinels is AGPL-3.0-only; its release includes corresponding source and notices. CivicAtlas remains a separate application with its existing license and data attribution.

`sentinels.toml` declares three real integration requirements: production `main` connects to `validate_snapshot`; the return from `load_manifest` reaches `build_observations`'s `manifest` argument; and the committed snapshot has the declared top-level JSON fields and types. Existing count and year fields deliberately remain strings. A PASS describes supported structural evidence, not successful execution on every branch. The source-only analyzer does not import or run the pipeline, and relevant ambiguity is reported as UNRESOLVED.

Data validation and byte-reproducibility remain independent gates. The adoption tests invoke the installed CLI against this configuration and inject defects into temporary copies of the real pipeline and artifact; reusable analyzer fixtures belong upstream. A fresh report can be saved with `uv run --locked sentinels check --root . --config sentinels.toml --json > artifacts/sentinels-report.json` after creating `artifacts/`.

The captured recording, transcript, cast, captions and hashes under `data/evidence/` are preserved historical evidence of the original local checker. Its generator and duplicate checker have been retired. Their source remains recoverable from [the original launch commit](https://github.com/goldenmonkeyrnd/civicatlas/tree/8522b545c2d8819e559ae421738d5cdc9d73797c). The current [editable lab](https://goldenmonkey.dev/labs/sentinels/v0.1.0/) produces new reports from visitor edits.

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

CivicAtlas code is MIT licensed; the separately consumed Sentinels tool is AGPL-3.0-only; fonts keep their SIL Open Font Licenses. Official public data is attributed in its manifest and source inspector.
