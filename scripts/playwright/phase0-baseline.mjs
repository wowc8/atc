import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'fs';
import path from 'path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const root = path.resolve(new URL('../..', import.meta.url).pathname);
const outDir = path.join(root, 'test-results', `phase0-baseline-${stamp}`);
const baseUrl = process.env.ATC_UI_URL || 'http://localhost:5173';
const apiUrl = process.env.ATC_API_URL || 'http://127.0.0.1:8420';
const codexCommand = process.env.ATC_CODEX_COMMAND || '/Users/mcole_studio/.local/bin/codex --dangerously-bypass-approvals-and-sandbox';

fs.mkdirSync(outDir, { recursive: true });

const checks = [];
const events = [];
const consoleMessages = [];
const failedRequests = [];
let screenshotIndex = 0;
let createdProject = null;

function note(name, detail = '') {
  events.push({ t: new Date().toISOString(), name, detail });
  console.log(`${name}${detail ? ` — ${detail}` : ''}`);
}

function check(name, ok, detail = '') {
  checks.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
}

async function screenshot(page, name) {
  const file = path.join(outDir, `${String(screenshotIndex++).padStart(2, '0')}-${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  note('SCREENSHOT', file);
  return file;
}

async function api(route, options = {}) {
  const res = await fetch(`${apiUrl}${route}`, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  });
  const text = await res.text();
  let body;
  try { body = text ? JSON.parse(text) : null; } catch { body = text; }
  return { ok: res.ok, status: res.status, body };
}

async function waitFor(name, fn, timeoutMs = 60000, intervalMs = 1500) {
  const start = Date.now();
  let last;
  while (Date.now() - start < timeoutMs) {
    last = await fn();
    if (last?.ok) {
      check(name, true, JSON.stringify(last.value ?? last));
      return last.value ?? last;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  check(name, false, `timed out; last=${JSON.stringify(last)}`);
  throw new Error(`${name} timed out`);
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });

page.on('console', (msg) => {
  if (['error', 'warning'].includes(msg.type())) {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  }
});
page.on('pageerror', (err) => consoleMessages.push({ type: 'pageerror', text: err.message }));
page.on('requestfailed', (req) => failedRequests.push({ url: req.url(), failure: req.failure()?.errorText }));
page.on('response', (res) => {
  const url = res.url();
  if (res.status() >= 400 && !url.includes('/@vite')) {
    failedRequests.push({ url, status: res.status() });
  }
});

try {
  const status = await api('/api/tower/status');
  check('Backend Tower status endpoint responds', status.ok, JSON.stringify(status.body));

  await page.goto(`${baseUrl}/dashboard`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByTestId('dashboard-page').or(page.getByText('Dashboard')).first().waitFor({ timeout: 15000 });
  check('Dashboard route loads', true, await page.title());
  await screenshot(page, 'dashboard');

  for (const label of ['⊞ Grid', '☰ Row', '▦ Board']) {
    const btn = page.getByRole('button', { name: label });
    await btn.click();
    await page.waitForTimeout(250);
    check(`Dashboard view toggle ${label}`, (await btn.getAttribute('aria-pressed')) === 'true');
  }
  await screenshot(page, 'dashboard-board-toggle');

  await page.getByTestId('settings-button').click();
  await page.getByTestId('settings-pane').waitFor({ timeout: 10000 });
  await page.locator('#provider-default').selectOption('codex');
  await page.locator('#provider-codex-command').fill(codexCommand);
  await page.locator('#provider-codex-command').blur();
  await page.waitForTimeout(1000);
  const providerStatus = await api('/api/settings/agent-provider');
  check('Default provider can be set/read as codex', providerStatus.ok && providerStatus.body?.default === 'codex', JSON.stringify(providerStatus.body));
  await screenshot(page, 'settings-codex');
  await page.getByTestId('close-settings-pane').click();

  const startButton = page.getByTestId('tower-panel-start');
  await startButton.waitFor({ timeout: 10000 });
  check('Tower Start visible on dashboard without selecting a project', await startButton.isVisible());
  check('Tower Start enabled without selecting a project', await startButton.isEnabled());
  await startButton.click();

  const towerStarted = await waitFor('Tower starts without active project', async () => {
    const res = await api('/api/tower/status');
    return {
      ok: res.ok && Boolean(res.body?.current_session_id) && ['planning', 'managing'].includes(res.body?.state),
      value: res.body,
    };
  }, 90000, 3000);
  check('Tower session reports a session id', Boolean(towerStarted.current_session_id), JSON.stringify(towerStarted));
  await screenshot(page, 'tower-started-no-active-project');

  const projectName = `Phase 0 Baseline ${stamp.slice(0, 19)}`;
  await page.getByRole('button', { name: '+ New Project' }).click();
  await page.locator('#project-name').fill(projectName);
  await page.locator('#project-desc').fill('Temporary project created by Phase 0 Playwright baseline validation.');
  await page.locator('#project-repo').fill('/tmp/atc-phase0-baseline-repo');
  await page.locator('#project-provider').selectOption('codex');
  const projectResponsePromise = page.waitForResponse((resp) => resp.url().includes('/api/projects') && resp.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create Project' }).click();
  const projectResponse = await projectResponsePromise;
  createdProject = await projectResponse.json();
  check('Temporary project created through UI with codex provider', projectResponse.ok() && createdProject?.agent_provider === 'codex', JSON.stringify(createdProject));

  await page.goto(`${baseUrl}/projects/${createdProject.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByTestId('tower-panel').waitFor({ timeout: 15000 });
  check('Project route loads with Tower panel', true, createdProject.id);
  await screenshot(page, 'project-tower-panel');

  const goal = 'Phase 0 baseline smoke: acknowledge this project, do not create files, and if a Leader is needed keep work non-destructive.';
  const goalResponse = await api('/api/tower/goal', { method: 'POST', body: JSON.stringify({ project_id: createdProject.id, goal }) });
  check('Tower goal endpoint accepts a project-scoped goal', goalResponse.ok && goalResponse.body?.status === 'accepted', JSON.stringify(goalResponse.body));

  await waitFor('Tower-driven goal creates Leader session', async () => {
    const res = await api('/api/tower/status');
    return { ok: res.ok && Boolean(res.body?.leader_session_id), value: res.body };
  }, 90000, 3000);
  await screenshot(page, 'tower-goal-leader-created');
} catch (err) {
  check('Phase 0 Playwright baseline completed without throw', false, err?.stack || err?.message || String(err));
  await screenshot(page, 'failure');
  process.exitCode = 1;
} finally {
  const finalStatus = await api('/api/tower/status').catch((err) => ({ ok: false, body: String(err) }));
  const sessions = await api('/api/orchestration/sessions').catch((err) => ({ ok: false, body: String(err) }));
  const projects = await api('/api/projects').catch((err) => ({ ok: false, body: String(err) }));
  const report = {
    outDir,
    baseUrl,
    apiUrl,
    codexCommand,
    createdProject,
    checks,
    events,
    consoleMessages,
    failedRequests,
    finalStatus,
    sessions,
    projects,
  };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`REPORT ${path.join(outDir, 'report.json')}`);
  await browser.close();
}

if (checks.some((entry) => !entry.ok)) {
  process.exitCode = 1;
}
