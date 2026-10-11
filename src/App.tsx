import { useEffect, useMemo, useRef, useState } from 'react';
import { geoMercator, geoPath } from 'd3-geo';
import { parseDetail, parseGeometry, parseOverview, parseSource, useResource } from './api';
import type { CountyGeometry } from './api';
import { track } from './analytics';
import type { CountyDetail, CountyOverview, CountySummary, Observation, Resource } from './types';

const portfolioUrl = 'https://goldenmonkey.dev';
const emailUrl = 'mailto:goldenmonkeyrnd@gmail.com?subject=GoldenMonkey.dev%20%E2%80%94%2030-minute%20conversation';
const integer = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const definitionLabels: Record<string, string> = { employment: 'Annual average jobs', establishments: 'Annual average establishments', annualPay: 'Average annual pay', employmentShare: 'Industry employment share', suppression: 'Disclosure rules' };
const dollars = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
const percent = new Intl.NumberFormat('en-US', { style: 'percent', maximumFractionDigits: 1 });

type SourceRequest = { id: string; title: string };
type MetricKey = 'employment' | 'establishments' | 'annualPay';

function industryName(value: string): string {
  return value.replace(/^NAICS\s+[0-9-]+\s+/, '');
}

function employmentShare(value: number): string {
  return value > 0 && value < .001 ? '<0.1%' : percent.format(value);
}

function unavailable(observation: Observation | undefined): string {
  return observation?.status === 'suppressed' ? 'Suppressed by BLS' : 'Not reported';
}

function metric(observation: Observation | undefined, key: MetricKey): string {
  if (!observation || observation.status !== 'available' || observation[key] === null) return unavailable(observation);
  return key === 'annualPay' ? dollars.format(observation[key]) : integer.format(observation[key]);
}

function Header() {
  return <>
    <a className="skip-link" href="#explorer">Skip to county explorer</a>
    <header className="site-header">
      <a className="wordmark" href={portfolioUrl} aria-label="GoldenMonkey.dev — Pauline Yeary’s portfolio">
        <img src="/favicon.svg" alt="" width="34" height="34" />
        <span>GoldenMonkey<span className="wordmark-dot">.dev</span></span>
      </a>
      <nav aria-label="Professional links">
        <a href={`${portfolioUrl}/resume/`}>Résumé</a>
        <a href="https://github.com/goldenmonkeyrnd/civicatlas" onClick={() => track('github_clicked')}>Source <span aria-hidden="true">↗</span></a>
        <a className="header-contact" href={emailUrl} onClick={() => track('email_clicked')}>Email Pauline <span aria-hidden="true">↗</span></a>
      </nav>
    </header>
  </>;
}

function ErrorNotice({ message, retry, heading = 'The data could not be loaded' }: { message: string; retry: () => void; heading?: string }) {
  return <div className="error-notice" role="alert">
    <strong>{heading}</strong>
    <p>{message}</p>
    <button className="button button-secondary" onClick={retry}>Try again</button>
  </div>;
}

function CountyMap({ geometry, retry, counties, selectedA, selectedB, activeSelection, onActiveSelection, onSelect }: {
  geometry: Resource<CountyGeometry>; retry: () => void; counties: CountySummary[]; selectedA: string; selectedB: string;
  activeSelection: 'a' | 'b'; onActiveSelection: (selection: 'a' | 'b') => void; onSelect: (fips: string) => void;
}) {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const byFips = useMemo(() => new Map(counties.map((county) => [county.fips, county])), [counties]);
  const largest = Math.max(...counties.map((county) => county.total.employment ?? 0));
  const path = useMemo(() => {
    if (geometry.state !== 'loaded') return null;
    const projection = geoMercator().fitExtent([[16, 16], [704, 384]], geometry.data);
    return geoPath(projection);
  }, [geometry]);
  const hovered = highlighted ? byFips.get(highlighted) : undefined;
  const selected = byFips.get(activeSelection === 'a' ? selectedA : selectedB);
  const color = (county: CountySummary | undefined): string => {
    if (!county || county.total.status !== 'available' || county.total.employment === null) return 'url(#unavailable-pattern)';
    const fraction = largest > 0 ? county.total.employment / largest : 0;
    const from = [218, 236, 244];
    const to = [36, 82, 189];
    return `rgb(${from.map((channel, index) => Math.round(channel + (to[index] - channel) * fraction)).join(',')})`;
  };
  const geometryMatches = geometry.state !== 'loaded' || geometry.data.features.every((feature) => byFips.has(feature.properties.GEOID));
  return <section className="map-panel" aria-labelledby="map-title">
    <div className="panel-heading">
      <div><p className="eyebrow">24 jurisdictions · one state</p><h2 id="map-title">Explore Maryland</h2></div>
      <span className="data-year">2024</span>
    </div>
    <fieldset className="map-choice">
      <legend>Choose which county the map changes</legend>
      <div className="segmented-control">
        <button className={activeSelection === 'a' ? 'active selection-a' : 'selection-a'} aria-pressed={activeSelection === 'a'} onClick={() => onActiveSelection('a')}><span className="county-marker marker-a" aria-hidden="true" />County A</button>
        <button className={activeSelection === 'b' ? 'active selection-b' : 'selection-b'} aria-pressed={activeSelection === 'b'} onClick={() => onActiveSelection('b')}><span className="county-marker marker-b" aria-hidden="true" />County B</button>
      </div>
    </fieldset>
    <div className="map-canvas">
      {geometry.state === 'loading' && <p className="loading-message" role="status">Loading county boundaries…</p>}
      {geometry.state === 'error' && <div className="map-unavailable"><strong>The map could not be loaded.</strong><p>{geometry.message} You can still compare counties with the selectors.</p><button className="button button-secondary" onClick={retry}>Reload map</button></div>}
      {geometry.state === 'loaded' && !geometryMatches && <p className="map-unavailable" role="alert">The map identifiers do not match the data. Use the county selectors to compare jurisdictions.</p>}
      {geometry.state === 'loaded' && geometryMatches && path && <svg viewBox="0 0 720 400" role="group" aria-label="Maryland county selection map">
        <defs><pattern id="unavailable-pattern" patternUnits="userSpaceOnUse" width="8" height="8"><rect width="8" height="8" fill="#F5EBDD" /><path d="M-2 2L2-2M0 8L8 0M6 10L10 6" stroke="#687782" strokeWidth="1" /></pattern></defs>
        {geometry.data.features.map((feature) => {
          const county = byFips.get(feature.properties.GEOID);
          const selection = feature.properties.GEOID === selectedA ? 'selected-a' : feature.properties.GEOID === selectedB ? 'selected-b' : '';
          return <path key={feature.properties.GEOID} d={path(feature) ?? undefined} fill={color(county)}
            className={`county-shape ${selection}`} role="button" tabIndex={0}
            aria-label={`Select ${county?.name ?? feature.properties.NAME} as county ${activeSelection.toUpperCase()}; ${county ? metric(county.total, 'employment') : 'unavailable'} annual average jobs`}
            onClick={() => onSelect(feature.properties.GEOID)}
            onKeyDown={(event) => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(feature.properties.GEOID); } }}
            onMouseEnter={() => setHighlighted(feature.properties.GEOID)} onMouseLeave={() => setHighlighted(null)}
            onFocus={() => setHighlighted(feature.properties.GEOID)} onBlur={() => setHighlighted(null)}>
            <title>{county?.name ?? feature.properties.NAME}</title>
          </path>;
        })}
      </svg>}
    </div>
    <p className="map-readout" aria-live="polite"><strong>{hovered?.name ?? selected?.name}</strong><span>{hovered ? `${metric(hovered.total, 'employment')} annual average jobs` : `Select a county on the map for county ${activeSelection.toUpperCase()}.`}</span></p>
    <div className="map-legend"><span>Annual average jobs</span><span className="color-scale" aria-hidden="true" /><span>0–{integer.format(largest)}</span></div>
    <p className="map-note">Color shows covered private-sector employment. Outlines mark your selections. Baltimore city is a separate jurisdiction from Baltimore County.</p>
    <details className="map-credit"><summary>About the boundaries</summary><p>2024 Census cartographic boundaries, 1:5 million scale. The map preserves county geometry and uses a geographic projection for display.</p><a href="https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html">Census boundary source <span aria-hidden="true">↗</span></a></details>
  </section>;
}

function CountySelect({ label, value, counties, onChange, marker }: { label: string; value: string; counties: CountySummary[]; onChange: (fips: string) => void; marker: 'a' | 'b' }) {
  return <div className="county-select"><label htmlFor={`county-${marker}`}><span className={`county-marker marker-${marker}`} aria-hidden="true" />{label}</label><select id={`county-${marker}`} value={value} onChange={(event) => onChange(event.target.value)}>{counties.map((county) => <option key={county.fips} value={county.fips}>{county.name}</option>)}</select></div>;
}

function useCounty(fips: string, revision: string) {
  const parser = useMemo(() => (value: unknown) => parseDetail(value, fips, revision), [fips, revision]);
  return useResource(`/api/v1/counties/${fips}`, parser);
}

function readyDetail(resource: Resource<CountyDetail>, fips: string): CountyDetail | undefined {
  return resource.state === 'loaded' && resource.data.county.fips === fips ? resource.data : undefined;
}

function IndustryValue({ observation, county, onSource }: { observation: Observation | undefined; county: CountySummary; onSource: (request: SourceRequest) => void }) {
  const content = <><span className="industry-jobs">{metric(observation, 'employment')}</span><span className="industry-share">{observation?.status === 'available' && observation.employmentShare != null ? `${employmentShare(observation.employmentShare)} of county jobs` : 'Share unavailable'}</span></>;
  return observation?.sourceId ? <button className="data-source-button" onClick={() => onSource({ id: observation.sourceId!, title: `${industryName(observation.industryName)} · ${county.name}` })}>{content}<span className="sr-only"> — Inspect the source for {industryName(observation.industryName)} in {county.name}; employment is annual average jobs.</span><span className="source-arrow" aria-hidden="true">↗</span></button> : <div className="industry-value">{content}</div>;
}

function Comparison({ overview, geometry, geometryRetry }: { overview: CountyOverview; geometry: Resource<CountyGeometry>; geometryRetry: () => void }) {
  const [selectedA, setSelectedA] = useState('24510');
  const [selectedB, setSelectedB] = useState('24005');
  const [activeSelection, setActiveSelection] = useState<'a' | 'b'>('a');
  const [source, setSource] = useState<SourceRequest | null>(null);
  const sortedCounties = useMemo(() => [...overview.counties].sort((a, b) => a.name.localeCompare(b.name)), [overview.counties]);
  const countyA = overview.counties.find((county) => county.fips === selectedA)!;
  const countyB = overview.counties.find((county) => county.fips === selectedB)!;
  const detailA = useCounty(selectedA, overview.revision);
  const detailB = useCounty(selectedB, overview.revision);
  const dataA = readyDetail(detailA.resource, selectedA);
  const dataB = readyDetail(detailB.resource, selectedB);
  const sectors = useMemo(() => {
    const merged = new Map<string, string>();
    for (const sector of [...(dataA?.sectors ?? []), ...(dataB?.sectors ?? [])]) merged.set(sector.industryCode, sector.industryName);
    return [...merged].sort(([a], [b]) => a.localeCompare(b, 'en', { numeric: true }));
  }, [dataA, dataB]);
  const changeCounty = (selection: 'a' | 'b', fips: string) => {
    if (selection === 'a') setSelectedA(fips); else setSelectedB(fips);
    track('comparison_used');
  };
  const openSource = (request: SourceRequest) => { setSource(request); track('source_opened'); };
  return <>
    <div className="explorer-layout" id="explorer" tabIndex={-1}>
      <CountyMap geometry={geometry} retry={geometryRetry} counties={overview.counties} selectedA={selectedA} selectedB={selectedB} activeSelection={activeSelection} onActiveSelection={setActiveSelection} onSelect={(fips) => changeCounty(activeSelection, fips)} />
      <section className="comparison-panel" aria-labelledby="comparison-title">
        <div className="panel-heading"><div><p className="eyebrow">Same year. Same definition.</p><h2 id="comparison-title">Compare counties</h2></div><span className="outline-label">Private sector</span></div>
        <div className="county-controls"><CountySelect label="County A" marker="a" value={selectedA} counties={sortedCounties} onChange={(fips) => changeCounty('a', fips)} /><CountySelect label="County B" marker="b" value={selectedB} counties={sortedCounties} onChange={(fips) => changeCounty('b', fips)} /></div>
        <p className="sr-only" aria-live="polite">Comparing {countyA.name} and {countyB.name}.</p>
        <table className="comparison-table">
          <caption className="sr-only">2024 private-sector county totals for {countyA.name} and {countyB.name}</caption>
          <thead><tr><th scope="col">County totals</th><th scope="col"><span className="county-marker marker-a" aria-hidden="true" />{countyA.name}</th><th scope="col"><span className="county-marker marker-b" aria-hidden="true" />{countyB.name}</th></tr></thead>
          <tbody>
            <tr><th scope="row">Annual average jobs</th><td>{metric(countyA.total, 'employment')}</td><td>{metric(countyB.total, 'employment')}</td></tr>
            <tr><th scope="row">Annual average establishments</th><td>{metric(countyA.total, 'establishments')}</td><td>{metric(countyB.total, 'establishments')}</td></tr>
            <tr><th scope="row">Average annual pay</th><td>{metric(countyA.total, 'annualPay')}</td><td>{metric(countyB.total, 'annualPay')}</td></tr>
          </tbody>
        </table>
        <div className="total-source-links">{[countyA, countyB].map((county, index) => county.total.sourceId ? <button key={`${county.fips}-${index}`} className="text-button" onClick={() => openSource({ id: county.total.sourceId!, title: `County totals · ${county.name}` })}>Inspect county {index === 0 ? 'A' : 'B'} source <span aria-hidden="true">↗</span></button> : <span key={`${county.fips}-${index}`}>County {index === 0 ? 'A' : 'B'} source unavailable</span>)}</div>
        <div className="definition-note">{selectedA === selectedB && <p className="same-county-note">Both selections currently refer to the same jurisdiction.</p>}<strong>What does a job count mean?</strong><p>These are annual average jobs at covered workplaces in the county. They count jobs, rather than employed residents. Pay is an annual average, rather than a median salary.</p></div>
      </section>
    </div>
    <section className="industry-panel" aria-labelledby="industry-title">
      <div className="industry-heading"><div><p className="eyebrow">A closer look at the mix</p><h2 id="industry-title">Which industries employ people?</h2></div><p>Each share uses that county’s private-sector total. Select a value to see its original source.</p></div>
      <div className="detail-status" aria-live="polite">
        {detailA.resource.state === 'error' && <ErrorNotice heading={`Industries for ${countyA.name} could not be loaded`} message={detailA.resource.message} retry={detailA.retry} />}
        {detailB.resource.state === 'error' && <ErrorNotice heading={`Industries for ${countyB.name} could not be loaded`} message={detailB.resource.message} retry={detailB.retry} />}
        {(!dataA && detailA.resource.state !== 'error' || !dataB && detailB.resource.state !== 'error') && <p className="loading-industry" role="status">Loading industry observations…</p>}
      </div>
      {dataA && dataB && <table className="industry-table">
        <caption className="sr-only">2024 annual average private-sector jobs and shares by NAICS sector for {countyA.name} and {countyB.name}</caption>
        <thead><tr><th scope="col">Industry <span className="column-note">NAICS sector</span></th><th scope="col"><span className="county-marker marker-a" aria-hidden="true" />{countyA.name}</th><th scope="col"><span className="county-marker marker-b" aria-hidden="true" />{countyB.name}</th></tr></thead>
        <tbody>{sectors.map(([code, name]) => <tr key={code}><th scope="row"><span className="sector-name">{industryName(name)}</span><span className="sector-code">{code}</span></th><td><IndustryValue observation={dataA.sectors.find((sector) => sector.industryCode === code)} county={countyA} onSource={openSource} /></td><td><IndustryValue observation={dataB.sectors.find((sector) => sector.industryCode === code)} county={countyB} onSource={openSource} /></td></tr>)}</tbody>
      </table>}
      <p className="disclosure-note">“Suppressed by BLS” means the figure is withheld to protect employer confidentiality. “Not reported” means no observation was supplied. Neither is counted as zero. Rounded shares and unavailable sectors may not sum to 100%.</p>
    </section>
    {source && <SourceInspector request={source} onClose={() => setSource(null)} />}
    <section className="project-evidence" aria-labelledby="evidence-title">
      <div><p className="eyebrow">I use AI. I check its work.</p><h2 id="evidence-title">Follow the evidence.</h2><p>Trace an observation to its source, inspect the pipeline, or watch a deliberately broken reference get caught before it reaches the data build.</p></div>
      <div className="evidence-links"><a className="button button-primary" href={`${portfolioUrl}/work/civicatlas/`}>Read the engineering story <span aria-hidden="true">↗</span></a><a className="button button-secondary" href={`${portfolioUrl}/work/agentic-engineering/`}>See the Sentinel walkthrough <span aria-hidden="true">↗</span></a><a className="text-button" href="https://github.com/goldenmonkeyrnd/civicatlas" onClick={() => track('github_clicked')}>Open the source on GitHub <span aria-hidden="true">↗</span></a></div>
    </section>
  </>;
}

function sourceValue(value: unknown): string {
  return value === '' ? '"" (empty string)' : typeof value === 'string' ? value : JSON.stringify(value, null, 2) ?? 'Not supplied';
}

function SourceInspector({ request, onClose }: { request: SourceRequest; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const parser = useMemo(() => (value: unknown) => {
    const source = parseSource(value);
    if (source.id !== request.id) throw new Error('The service returned a different source record. Try again.');
    return source;
  }, [request.id]);
  const { resource, retry } = useResource(`/api/v1/sources/${encodeURIComponent(request.id)}`, parser);
  useEffect(() => { const element = dialog.current; element?.showModal(); return () => element?.close(); }, []);
  const close = () => { dialog.current?.close(); onClose(); };
  return <dialog ref={dialog} className="source-dialog" aria-labelledby="source-title" onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="source-dialog-header"><div><p className="eyebrow">The number has a paper trail</p><h2 id="source-title">Inspect the source</h2></div><button className="close-dialog" aria-label="Close source inspector" onClick={close}><span aria-hidden="true">×</span></button></div>
    <p className="source-subtitle">{request.title}</p>
    {resource.state === 'loading' && <p className="loading-message" role="status">Loading the original fields and definitions…</p>}
    {resource.state === 'error' && <ErrorNotice message={resource.message} retry={retry} heading="The source could not be loaded" />}
    {resource.state === 'loaded' && <>
      <div className="source-origin"><strong>U.S. Bureau of Labor Statistics</strong><span>QCEW · 2024 annual · private ownership</span><a href={resource.data.url} target="_blank" rel="noopener noreferrer">Open the original CSV <span aria-hidden="true">↗</span><span className="sr-only"> (new tab)</span></a></div>
      <h3>Definitions</h3>
      <dl className="source-definitions">{Object.entries(resource.data.definitions).map(([key, value]) => <div key={key}><dt>{definitionLabels[key] ?? key.replaceAll('_', ' ')}</dt><dd>{sourceValue(value)}</dd></div>)}</dl>
      <h3>Transformation</h3><p className="source-transformation">{resource.data.transformation}</p>
      <h3>Original source fields</h3>
      <div className="raw-fields"><table><caption className="sr-only">Original fields from the BLS CSV observation</caption><thead><tr><th scope="col">Field</th><th scope="col">Original value</th></tr></thead><tbody>{Object.entries(resource.data.raw).map(([key, value]) => <tr key={key}><th scope="row">{key}</th><td>{sourceValue(value)}</td></tr>)}</tbody></table></div>
      <details className="source-reproducibility"><summary>Snapshot and integrity details</summary><dl><dt>Retrieved</dt><dd><time dateTime={resource.data.retrievedAt}>{new Date(resource.data.retrievedAt).toLocaleString('en-US', { timeZone: 'UTC' })} UTC</time></dd><dt>Source identifier</dt><dd><code>{resource.data.id}</code></dd><dt>SHA-256 of original source</dt><dd><code>{resource.data.sha256}</code></dd></dl></details>
    </>}
  </dialog>;
}

function Footer() {
  return <footer className="site-footer"><div><a className="footer-name" href={portfolioUrl}>Pauline Yeary <span aria-hidden="true">↗</span></a><p>Data & AI Engineer · Golden Monkey Research and Development LLC</p></div><div className="footer-links"><a href={emailUrl} onClick={() => track('email_clicked')}>Let’s talk</a><a href={`${portfolioUrl}/downloads/Pauline_Yeary_Resume.pdf`} onClick={() => track('resume_downloaded')}>Download résumé</a><a href={`${portfolioUrl}/privacy/`}>Privacy & analytics</a></div><p className="footer-credit">Created with AI assistance. Directed and reviewed by Pauline Yeary. Source: <a href="https://www.bls.gov/cew/">BLS QCEW</a> and <a href="https://www.census.gov/geographies/mapping-files/time-series/geo/cartographic-boundary.html">U.S. Census Bureau</a>.</p></footer>;
}

export default function App() {
  const overview = useResource('/api/v1/counties', parseOverview);
  const geometry = useResource('/data/maryland-counties.geojson', parseGeometry);
  return <>
    <Header />
    <main>
      <section className="intro" aria-labelledby="page-title"><div className="intro-copy"><p className="eyebrow"><span className="atlas-label">CivicAtlas</span> A public-sector data explorer</p><h1 id="page-title">Maryland,{' '}<br /><span>at work.</span></h1><p className="intro-description">Where is private-sector employment concentrated? Compare two counties, explore their industries, and inspect the evidence behind every number.</p></div><div className="snapshot-card"><span className="snapshot-year">2024</span><span>Private-sector employment snapshot</span><p>Annual averages across Maryland’s 23 counties and Baltimore city.</p><a href="https://www.bls.gov/cew/additional-resources/open-data/csv-data-slices.htm">About the BLS data <span aria-hidden="true">↗</span></a></div></section>
      {overview.resource.state === 'loading' && <div className="initial-state" id="explorer" tabIndex={-1}><p className="loading-message" role="status">Loading Maryland’s employment snapshot…</p></div>}
      {overview.resource.state === 'error' && <div className="initial-state" id="explorer" tabIndex={-1}><ErrorNotice message={overview.resource.message} retry={overview.retry} /><p>The portfolio, résumé and email links remain available while the data service recovers.</p><a href={emailUrl} className="button button-primary" onClick={() => track('email_clicked')}>Email Pauline</a></div>}
      {overview.resource.state === 'loaded' && <Comparison overview={overview.resource.data} geometry={geometry.resource} geometryRetry={geometry.retry} />}
    </main>
    <Footer />
  </>;
}
