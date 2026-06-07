import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const outDir = path.resolve('../screenshots/playwright-atc-' + new Date().toISOString().replace(/[:.]/g, '-'));
fs.mkdirSync(outDir, { recursive: true });
const errors = [];
const requests = [];
const checks = [];

function record(name, ok, detail = '') {
  checks.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ' — ' + detail : ''}`);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 });
page.on('console', msg => {
  const text = msg.text();
  if (['error', 'warning'].includes(msg.type())) errors.push({ type: msg.type(), text });
});
page.on('pageerror', err => errors.push({ type: 'pageerror', text: err.message }));
page.on('requestfailed', req => requests.push({ url: req.url(), failure: req.failure()?.errorText }));
page.on('response', res => {
  const url = res.url();
  if (res.status() >= 400 && !url.includes('/@vite')) requests.push({ url, status: res.status() });
});

async function screenshot(name) {
  const file = path.join(outDir, `${String(checks.length).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`SCREENSHOT ${file}`);
}

try {
  await page.goto('http://localhost:5173/dashboard', { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByTestId('dashboard-page').or(page.getByText('Dashboard')).first().waitFor({ timeout: 15000 });
  record('Dashboard route loads', true, await page.title());
  await screenshot('dashboard-initial');

  for (const label of ['⊞ Grid', '☰ Row', '▦ Board']) {
    const btn = page.getByRole('button', { name: label });
    await btn.click();
    await page.waitForTimeout(300);
    const pressed = await btn.getAttribute('aria-pressed');
    record(`Dashboard view toggle ${label}`, pressed === 'true', `aria-pressed=${pressed}`);
    await screenshot(`dashboard-${label.replace(/[^a-zA-Z]+/g, '').toLowerCase()}`);
  }

  await page.getByRole('button', { name: '+ New Project' }).click();
  await page.waitForSelector('[data-testid="create-project-modal"]');
  record('Create Project modal opens', true);
  await screenshot('create-project-modal');

  await page.getByRole('button', { name: 'Create Project' }).click();
  const validation = await page.locator('#project-name').evaluate(el => el.matches(':invalid'));
  record('Create Project required-name validation works without creating data', validation === true, `input invalid=${validation}`);
  await page.keyboard.press('Escape');
  await page.waitForSelector('[data-testid="create-project-modal"]', { state: 'detached' });
  record('Create Project modal closes via Escape', true);

  await page.goto('http://localhost:5173/usage', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('text=Usage', { timeout: 15000 });
  record('Usage route loads', true);
  await screenshot('usage');
  for (const p of ['30d', '90d', '7d']) {
    await page.getByRole('button', { name: p }).click();
    await page.waitForTimeout(500);
    record(`Usage period button ${p} clickable`, true);
  }

  await page.goto('http://localhost:5173/context', { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForLoadState('networkidle');
  const bodyText = (await page.locator('body').innerText()).slice(0, 500).replace(/\s+/g, ' ');
  record('Context route reachable', bodyText.length > 0, bodyText);
  await screenshot('context');

  const navDashboard = page.getByText('Dashboard').first();
  if (await navDashboard.count()) {
    await navDashboard.click();
    await page.waitForURL(/dashboard/);
    record('Sidebar navigation back to Dashboard works', true);
  } else {
    record('Sidebar navigation back to Dashboard works', false, 'Dashboard nav text not found');
  }

} catch (err) {
  record('Playwright script completed without throw', false, err?.message ?? String(err));
  await screenshot('failure');
} finally {
  await browser.close();
}

const report = { outDir, checks, errors, requests };
fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
console.log('\nREPORT ' + path.join(outDir, 'report.json'));
if (checks.some(c => !c.ok)) process.exitCode = 1;
