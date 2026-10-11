type EventName = 'comparison_used' | 'source_opened' | 'github_clicked' | 'resume_downloaded' | 'walkthrough_started' | 'email_clicked' | 'conference_viewed';

function allowsAnalytics(): boolean {
  return !(navigator as Navigator & { globalPrivacyControl?: boolean }).globalPrivacyControl && navigator.doNotTrack !== '1' && navigator.doNotTrack !== 'yes';
}

export function track(event: EventName): void {
  if (!allowsAnalytics()) return;
  void fetch('/api/events', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, keepalive: true,
    body: JSON.stringify({ event, surface: 'atlas', campaign: 'nava-2026' }),
  }).catch(() => { /* A telemetry failure never interrupts the application. */ });
}

export function mountWebAnalytics(): void {
  if (!allowsAnalytics()) return;
  const script = document.createElement('script');
  script.src = 'https://static.cloudflareinsights.com/beacon.min.js';
  script.defer = true;
  script.dataset.cfBeacon = JSON.stringify({ token: 'e59e8755f32c444a96b2822c51924e14' });
  document.head.append(script);
}
