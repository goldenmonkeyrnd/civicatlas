-- A frozen, read-only public data snapshot. No visitor or contact records.
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS counties (
  fips TEXT PRIMARY KEY CHECK(length(fips) = 5 AND fips GLOB '24[0-9][0-9][0-9]'),
  name TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observations (
  fips TEXT NOT NULL REFERENCES counties(fips),
  industry_code TEXT NOT NULL,
  industry_name TEXT NOT NULL,
  employment REAL,
  establishments REAL,
  annual_pay REAL,
  status TEXT NOT NULL CHECK(status IN ('available','suppressed','missing')),
  source_id TEXT NOT NULL REFERENCES sources(id),
  PRIMARY KEY(fips, industry_code),
  CHECK(employment IS NULL OR employment >= 0),
  CHECK(establishments IS NULL OR establishments >= 0),
  CHECK(annual_pay IS NULL OR annual_pay >= 0),
  CHECK(status = 'available' OR (employment IS NULL AND establishments IS NULL AND annual_pay IS NULL))
);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS observations_industry ON observations(industry_code);
