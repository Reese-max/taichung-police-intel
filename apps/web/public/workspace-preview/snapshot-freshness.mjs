function refreshSnapshotAge() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ''));
  if (params.get('mode') !== 'snapshot') return;
  document.querySelector('[data-action="reload"]')?.click();
}

const timer = setInterval(refreshSnapshotAge, 60_000);
window.addEventListener('pagehide', () => clearInterval(timer), { once: true });
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshSnapshotAge();
});
// Testable same-origin hook; no data is accepted from the event.
document.addEventListener('govintel:refresh-snapshot-age', refreshSnapshotAge);
