import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const root = path.resolve(new URL('../..', import.meta.url).pathname);
const outDir = path.join(root, 'test-results', `phase5-runtime-boundary-${stamp}`);
const baseUrl = process.env.ATC_UI_URL || 'http://localhost:5173';
const apiUrl = process.env.ATC_API_URL || 'http://127.0.0.1:8420';
const codexCommand = process.env.ATC_CODEX_COMMAND || '/Users/mcole_studio/.local/bin/codex';

fs.mkdirSync(outDir, { recursive: true });

const checks = [];
const events = [];
const consoleMessages = [];
const failedRequests = [];
let screenshotIndex = 0;
let project = null;
let leaderSessionId = null;

function check(name, ok, detail = '') {
  checks.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) throw new Error(`${name}: ${detail}`);
}

function note(name, detail = '') {
  events.push({ t: new Date().toISOString(), name, detail });
  console.log(`${name}${detail ? ` — ${detail}` : ''}`);
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
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  return { ok: res.ok, status: res.status, body };
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1365, height: 768 }, deviceScaleFactor: 1 });
page.on('console', (msg) => {
  if (['error', 'warning'].includes(msg.type())) {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  }
});
page.on('pageerror', (err) => consoleMessages.push({ type: 'pageerror', text: err.message }));
page.on('requestfailed', (req) => failedRequests.push({ url: req.url(), failure: req.failure()?.errorText }));
page.on('response', (res) => {
  if (res.status() >= 400 && !res.url().includes('/@vite')) {
    failedRequests.push({ url: res.url(), status: res.status() });
  }
});

try {
  const health = await api('/api/health');
  check('Backend health responds', health.ok, JSON.stringify(health.body));

  const provider = await api('/api/settings/agent-provider', {
    method: 'PUT',
    body: JSON.stringify({ default: 'codex', codex_command: codexCommand }),
  });
  check('Default provider configured for codex', provider.ok, JSON.stringify(provider.body));

  const projectResp = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({
      name: `Phase 5 Runtime Boundary ${stamp.slice(0, 19)}`,
      description: 'Temporary Phase 5 validation project.',
      agent_provider: 'codex',
      auto_kickoff: false,
    }),
  });
  check('Temporary project created', projectResp.ok, JSON.stringify(projectResp.body));
  project = projectResp.body;

  const leaderStart = await api(`/api/projects/${project.id}/leader/start`, {
    method: 'POST',
    body: JSON.stringify({ goal: 'Phase 5 validation: runtime delivery boundary.' }),
  });
  check('Leader start endpoint succeeds', leaderStart.ok, JSON.stringify(leaderStart.body));
  leaderSessionId = leaderStart.body?.session_id;
  check('Leader start returns session id', Boolean(leaderSessionId), JSON.stringify(leaderStart.body));

  const manager = await api(`/api/projects/${project.id}/manager`);
  check('Leader manager endpoint returns active leader', manager.ok && Boolean(manager.body?.id), JSON.stringify(manager.body));

  const message = await api(`/api/projects/${project.id}/leader/message`, {
    method: 'POST',
    body: JSON.stringify({ message: 'Phase 5 validation ping: report your runtime delivery state.' }),
  });
  check('Leader message endpoint succeeds', message.ok, JSON.stringify(message.body));
  check(
    'Leader message no longer reports generic sent status',
    message.body?.status !== 'sent',
    JSON.stringify(message.body),
  );
  check(
    'Leader message exposes runtime delivery object',
    Boolean(message.body?.delivery?.status && message.body?.delivery?.stage && message.body?.delivery?.verdict),
    JSON.stringify(message.body),
  );
  check(
    'Runtime delivery status is operator-truthful',
    ['queued', 'delivered', 'confirmed', 'blocked', 'failed'].includes(message.body?.delivery?.status),
    JSON.stringify(message.body),
  );
  check(
    'Runtime delivery reason code is surfaced',
    Boolean(message.body?.delivery?.reason_code),
    JSON.stringify(message.body),
  );

  const managerAfterMessage = await api(`/api/projects/${project.id}/manager`);
  check(
    'Leader manager endpoint remains available after delivery',
    managerAfterMessage.ok && managerAfterMessage.body?.session_id === leaderSessionId,
    JSON.stringify(managerAfterMessage.body),
  );

  await page.goto(`${baseUrl}/projects/${project.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await screenshot(page, 'project-runtime-boundary-loaded');

  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await screenshot(page, 'dashboard-runtime-boundary-project-visible');

  check('No failed browser requests', failedRequests.length === 0, JSON.stringify(failedRequests));
  const pageErrors = consoleMessages.filter((entry) => entry.type === 'pageerror');
  check('No page errors', pageErrors.length === 0, JSON.stringify(pageErrors));

  const report = {
    outDir,
    baseUrl,
    apiUrl,
    codexCommand,
    project,
    leaderSessionId,
    messageDelivery: message.body,
    checks,
    events,
    consoleMessages,
    failedRequests,
  };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`REPORT ${path.join(outDir, 'report.json')}`);
} catch (err) {
  checks.push({
    name: 'Phase 5 Playwright validation completed without throw',
    ok: false,
    detail: err?.stack || err?.message || String(err),
  });
  await screenshot(page, 'failure');
  fs.writeFileSync(
    path.join(outDir, 'report.json'),
    JSON.stringify({ outDir, checks, events, consoleMessages, failedRequests, project, leaderSessionId }, null, 2),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}

if (checks.some((entry) => !entry.ok)) {
  process.exitCode = 1;
}
