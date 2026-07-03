import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'fs';
import path from 'path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const outDir = path.resolve('test-results', `phase4-codex-token-visibility-${stamp}`);
fs.mkdirSync(outDir, { recursive: true });
const checks = [];
const consoleMessages = [];
const failedRequests = [];
const pageErrors = [];

function check(name, ok, detail = '') {
  checks.push({ name, ok, detail });
  if (!ok) console.error(`FAIL ${name}: ${detail}`);
}

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.on('console', (msg) => {
  if (['error', 'warning'].includes(msg.type())) {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  }
});
page.on('pageerror', (err) => pageErrors.push(err.message));
page.on('requestfailed', (request) => {
  failedRequests.push({ url: request.url(), failure: request.failure()?.errorText ?? 'unknown' });
});

const codexStatus = {
  enabled: true,
  running: true,
  sessions_glob: '~/.codex/sessions/**/*.jsonl',
  poll_interval_seconds: 30,
  last_started_at: '2026-07-03T16:48:00Z',
  last_finished_at: '2026-07-03T16:48:01Z',
  last_inserted_events: 0,
  last_discovered_files: 7,
  last_error: null,
};

await page.route('**/api/**', async (route) => {
  const url = route.request().url();
  if (url.includes('/api/usage/tokens/sync-codex/status')) {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(codexStatus) });
    return;
  }
  if (url.includes('/api/usage/tokens/sync-codex')) {
    codexStatus.last_inserted_events = 2;
    codexStatus.last_finished_at = '2026-07-03T16:49:01Z';
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ inserted_events: 2, enabled: true }) });
    return;
  }
  if (url.includes('/api/usage/summary')) {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ today_tokens: 43210, month_tokens: 987654 }) });
    return;
  }
  if (url.includes('/api/usage/tokens')) {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ date: '2026-07-03', input_tokens: 40000, output_tokens: 3210, model: 'gpt-5.5' }]) });
    return;
  }
  if (url.includes('/api/usage/resources')) {
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([{ timestamp: '2026-07-03T16:48:00Z', cpu_pct: 12, ram_mb: 2048 }]) });
    return;
  }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
});

try {
  await page.goto('http://127.0.0.1:5173/usage', { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'Usage', exact: true }).waitFor();
  await page.getByRole('heading', { name: 'Codex Token Sync' }).waitFor();
  await page.getByText('~/.codex/sessions/**/*.jsonl').waitFor();
  await page.screenshot({ path: path.join(outDir, 'usage-codex-sync-status.png'), fullPage: true });

  await page.getByRole('button', { name: 'Sync now' }).click();
  await page.getByText('Inserted 2 token events.').waitFor();
  await page.screenshot({ path: path.join(outDir, 'usage-codex-sync-after-manual.png'), fullPage: true });

  check('Usage route loads', await page.getByTestId('usage-page').isVisible());
  check('Codex sync card visible', await page.getByRole('heading', { name: 'Codex Token Sync' }).isVisible());
  check('Codex source glob visible', await page.getByText('~/.codex/sessions/**/*.jsonl').isVisible());
  check('Manual sync feedback visible', await page.getByText('Inserted 2 token events.').isVisible());
} catch (err) {
  check('Playwright script completed', false, err?.stack || err?.message || String(err));
}

await browser.close();
const report = { outDir, checks, consoleMessages, failedRequests, pageErrors };
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
const failed = checks.filter((c) => !c.ok);
console.log(`REPORT ${path.join(outDir, 'report.json')}`);
console.log(`${checks.length - failed.length}/${checks.length} checks passed`);
if (failed.length || pageErrors.length) process.exit(1);
