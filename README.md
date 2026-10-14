# CivicAtlas · Maryland Outreach Lab

**Where could three additional outreach sites bring benefits assistance closer?**

[Open the application](https://atlas.goldenmonkey.dev/?view=plan) · [Engineering story](https://goldenmonkey.dev/work/civicatlas/) · [Conference introduction](https://goldenmonkey.dev/summit/2026/)

An inspectable public-data decision tool by Pauline Yeary, built with React, TypeScript, MapLibre, a browser Web Worker, Python/DuckDB, and Cloudflare Workers/D1. It covers 4,079 Maryland block groups, 1,475 tracts, and all 24 county-equivalent jurisdictions. Baltimore city (24510) and Baltimore County (24005) remain separate.

## Try a three-minute demonstration

1. Open **Plan outreach** in Baltimore City. Defaults: three proposed library sites, three modeled road miles, total households.
2. Review baseline, proposed coverage, and each suggested site's contribution. Replace a site, change the distance, or close a road and compare the consequence.
3. Switch to **Explore** for income, poverty, rent burden, vehicles, internet, household composition, and workforce estimates. Employment/pay remain county observations; commuting shows 2021 Maryland home–work job relationships.
4. Inspect a neighborhood's source records. Open **Evidence** for address corrections, closures, exclusions, source hashes, and coverage.
5. Export a comparison CSV or scenario JSON, share a versioned link, and print the briefing. Import JSON to replay the same release, graph, sites, distance, and closures.

Equivalent neighborhood and facility tables remain usable when WebGL is unavailable. Reduced motion disables automatic flow animation; pause, reset, and replay also control it.

Use **Try a road disruption** for a replayable, revision-checked East University Parkway example. It moves 573 represented households beyond the distance threshold; **Reopen all** restores the original results. The example resets to the default Baltimore measure/sites before applying its saved closure.

## Run locally, including without conference connectivity

Use Python 3.12, uv, and Bun 1.3.4. Install dependencies and seed once while connected:

```sh
uv sync --locked
bun install --frozen-lockfile
bun run db:local
bun run db:outreach:local
bun run dev -- --host 127.0.0.1 --port 5175
```

Open `http://127.0.0.1:5175/?view=plan`. Both databases, fonts, boundaries, roads, and commuting assets are local. Leave the terminal running; Ctrl-C stops it. After installation/seeding, launch requires no source API key or internet. Public and local apps use the same pinned release.

An unavailable-data message usually means a seed command was omitted. A revision mismatch means the scenario belongs to another release; start a new scenario or use its archived release. Choose another port if 5175 is occupied. Reseeding restores the committed local data.

## Data and reproducible publication

`/media/user/data/babylon-data` is the primary read-only source library. Acquisition extracts bounded Maryland records. `data/outreach/raw/manifest.json` and `data/raw/manifest.json` pin URLs, periods, retrieval evidence, hashes, and transformations. Ordinary builds use committed bounded inputs, separately from acquisition.

ACS observations retain estimates, margins of error, denominators, geography, and observed-zero versus unavailable/suppressed states. Additional block-group tables: C17002 (poverty), B25070 (rent burden), B25044 (vehicles), B28002 (internet). Each measure has its own universe. Total households, households without vehicles, and households without internet are alternative demand measures; overlapping groups are never added together.

County context contains 2010–2024 QCEW private-sector history and the existing 2024 sector comparisons. Employment means covered workplace jobs; pay is nominal annual pay per job. LODES 2021 includes relationships with both home and workplace in Maryland. These are jobs, not observed routes; top-flow truncation reports omitted jobs.

Current library directories and DHS records reconcile the local facility inventory: 172 verified library candidates and 38 verified benefits sites across all jurisdictions. Six documented closures and 51 unresolved/obsolete/non-public records remain inspectable and excluded. Thirty explicit address corrections retain source evidence; six unresolved corrections remain excluded. Libraries without documented assistance are candidates rather than existing services.

```sh
uv run --locked python -m pipeline.build --check
uv run --locked python -m pipeline.outreach_build --check
uv run --locked python tools/stage_outreach.py --check
uv run --locked python -m unittest discover -s tests/python -v
uv run --locked sentinels check --root . --config sentinels.toml --json
bun run check
bun run build
```

Python gates verify raw hashes, coverage, joins, statistical transformations, exact SQL round trips, and byte reproduction. Worker tests execute the actual SQLite seed. Engine tests cover directed roads, disconnected components, closures, deterministic tie-breaking, replay, and no double counting. Sentinels v0.2.0 checks five structural contracts and the explicit pipeline coverage ledger. Structural PASS and runtime verification remain separate evidence.

`data/qualification/` records actual verification. Original and reduced Baltimore graphs preserve coverage/recommendations at one, three, and ten miles. Across all jurisdictions, 120 scenario runs check replay, conservation, additions, closures, and alternative measures. On the Ryzen 5 2600 development machine, default Baltimore engine execution took 701 ms; the slowest county took 1,262 ms. Chromium production-preview qualification became usable 4.36 seconds after required input responses (6.52 seconds cold-page total), and includes visible polygons, proposed pins, route flows, source/API/profile/CSV traces, print figures, offline use, and WebGL-disabled tables. Measurements cover this development host and emulated mobile layouts.

Graph preparation requires the six pinned Geofabrik extracts in `data/outreach/roads-manifest.json` under `artifacts/roads/`, plus the original county shape source in the read-only corpus. Run `uv run --locked python tools/build_roads.py`, then `uv run --locked python tools/stage_outreach.py`. Raw PBFs and uncompressed graphs are ignored; bounded gzip graphs and compressed/decoded hashes are committed. Source updates require acquisition, a new revision, graph regeneration, reconciliation, and verification before publication.

Publication rejects changed bytes at an existing release asset path. A published namespace stays immutable; changed data or graph transformations require a new release revision.

## What the model calculates

Each block group uses its Census internal representative point snapped to an existing motor-road node within 500 meters. Shared OSM nodes establish junctions, preserving grade separation, recorded motor access, and one-way direction. County graphs include 17 km of surrounding roads, beyond the supported ten-mile threshold. Degree-two corridors and branches without attached neighborhoods/facilities are reduced while preserving distance/connectivity. Hypothetical locations snap to a retained node within 500 meters.

Verified benefits sites mapped in the surrounding road graph form the baseline, including nearby Maryland jurisdictions. Candidates come from the selected county. Greedy coverage adds the candidate covering the most previously uncovered represented households, repeats, and breaks ties by stable ID. This transparent heuristic does not establish optimal placement.

Results describe **households represented by points within the chosen modeled road distance**. Unmatched, disconnected, unavailable, and beyond-threshold observations remain separate. Results do not establish eligibility, attendance, transit access, site capacity, travel time, or driving directions. Turn restrictions, schedules, and traffic are not modeled.

Demand follows valid directed routes. Physarum-inspired conductivity follows `D_next = 0.8 D + 0.2 |Q|`; animation does not determine statistics. Scenario version 1 records data/graph revisions, county, one measure, sites, hypothetical locations, closures, and distance. Obsolete workers are cancelled and stale results rejected.

## API, assets, and deployment

Typed `/api/v2` endpoints provide catalog, areas filtered by county/level, facilities, county history, source inspection with optional raw fields, and release health. Invalid identifiers and duplicate/unknown parameters return explicit errors. Queries are parameterized; public SQL submission is unavailable. Existing `/api/v1` employment endpoints remain supported.

D1 stores observations and provenance. Ordered bounded SQL fragments reassemble the catalog exactly. Immutable gzip map/graph assets are self-hosted under `/data/outreach/{revision}/` and delivered by the same-origin Worker. R2 is disabled on this account; this release uses existing Workers Assets and verifies immutable publication hashes without adding a paid service.

GitHub Actions checks data, contracts, TypeScript, Worker/engine tests, compressed assets, and builds before deployment. The independent import workflow exports the existing database for recovery and imports outreach into separate `civicatlas-outreach-20261014`. Validate the new database/revision before switching the app. Deployments do not acquire or reseed data.

Authenticated maintainers can run `bun run db:outreach:import` and `bun run deploy`; tokens stay in GitHub environment secrets. Code recovery: `bunx wrangler deployments list`, then `bunx wrangler rollback <version-id>`. Code rollback does not restore a database; retain the SQL export and use D1 Time Travel or a verified backup when needed. The original county database remains independent.

## Attribution and privacy

Application code is MIT. Derived street graph data is © OpenStreetMap contributors, ODbL 1.0, downloadable with its manifest. Census, BLS, Maryland, DHS, and libraries are attributed in Evidence. Fonts retain SIL Open Font Licenses. Sentinels is separately consumed under AGPL-3.0-only with corresponding release source and notices.

Existing aggregate events retain their privacy contract: event/surface/campaign labels only, no persistent visitor ID or contact record, GPC/DNT honored, telemetry faults never block use. Cloudflare processes ordinary connection metadata to serve the app. Aggregate actions cannot identify recruiters or establish a linked visitor funnel.
