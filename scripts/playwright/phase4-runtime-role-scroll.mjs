import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const root = path.resolve(new URL('../..', import.meta.url).pathname);
const outDir = path.join(root, 'test-results', `phase4-runtime-role-scroll-${stamp}`);
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
let towerSession = null;
let leaderSessionId = null;
let leaderId = null;
let aceSessionId = null;

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
}

function readRoleFile(sessionId) {
  const file = path.join('/tmp/atc-agents', sessionId, 'AGENTS.md');
  const content = fs.readFileSync(file, 'utf8');
  return { file, content };
}

function assertRoleFile(sessionId, role, expectedSnippets) {
  const { file, content } = readRoleFile(sessionId);
  check(`${role} AGENTS.md exists`, fs.existsSync(file), file);
  for (const snippet of expectedSnippets) {
    check(`${role} role file contains ${snippet}`, content.includes(snippet), file);
  }
  return file;
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 }, deviceScaleFactor: 1 });
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

  const towerStart = await api('/api/tower/start', { method: 'POST', body: JSON.stringify({}) });
  check('Tower start endpoint succeeds', towerStart.ok, JSON.stringify(towerStart.body));
  towerSession = towerStart.body?.session_id;
  check('Tower start returns session id', Boolean(towerSession), JSON.stringify(towerStart.body));
  const towerRoleFile = assertRoleFile(towerSession, 'Tower', [
    'If Asked \'What Is Your Role?\'',
    'ATC Tower',
    'top-level user-facing orchestration agent',
    'must not write code',
  ]);

  const projectResp = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({
      name: `Phase 4 Role Scroll ${stamp.slice(0, 19)}`,
      description: 'Temporary Phase 4 validation project.',
      agent_provider: 'codex',
    }),
  });
  check('Temporary project created', projectResp.ok, JSON.stringify(projectResp.body));
  project = projectResp.body;

  const leaderStart = await api(`/api/projects/${project.id}/leader/start`, {
    method: 'POST',
    body: JSON.stringify({ goal: 'Phase 4 validation: role awareness and UI scroll.' }),
  });
  check('Leader start endpoint succeeds', leaderStart.ok, JSON.stringify(leaderStart.body));
  leaderSessionId = leaderStart.body?.session_id;
  check('Leader start returns session id', Boolean(leaderSessionId), JSON.stringify(leaderStart.body));

  const manager = await api(`/api/projects/${project.id}/manager`);
  check('Leader manager endpoint returns Leader id', manager.ok && Boolean(manager.body?.id), JSON.stringify(manager.body));
  leaderId = manager.body?.id;
  const leaderRoleFile = assertRoleFile(leaderId, 'Leader', [
    'If Asked \'What Is Your Role?\'',
    'ATC Leader',
    'project-level coordinator',
    'must not write code',
  ]);

  const decompose = await api(`/api/projects/${project.id}/leader/decompose`, {
    method: 'POST',
    body: JSON.stringify({
      goal: 'Phase 4 validation task graph',
      task_specs: [
        { title: 'Validate Ace role awareness', description: 'Confirm Ace AGENTS.md role text exists.' },
      ],
    }),
  });
  check('Leader decompose creates task graph', decompose.ok, JSON.stringify(decompose.body));

  const spawn = await api(`/api/projects/${project.id}/leader/spawn-aces`, { method: 'POST' });
  check('Leader spawn-aces endpoint succeeds', spawn.ok, JSON.stringify(spawn.body));
  aceSessionId = spawn.body?.spawned?.[0]?.ace_session_id;
  check('Spawned Ace session id returned', Boolean(aceSessionId), JSON.stringify(spawn.body));
  const aceRoleFile = assertRoleFile(aceSessionId, 'Ace', [
    'If Asked \'What Is Your Role?\'',
    'ATC Ace',
    'project execution agent',
    'must not manage',
  ]);

  await page.goto(`${baseUrl}/projects/${project.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.locator('.project-view__left').waitFor({ timeout: 15000 });
  await screenshot(page, 'project-left-column-before-scroll');

  const scrollProbe = await page.evaluate(() => {
    const left = document.querySelector('.project-view__left');
    if (!(left instanceof HTMLElement)) return { ok: false, reason: 'left column missing' };
    const filler = document.createElement('div');
    filler.textContent = 'scroll probe for aces/workers reachability';
    filler.style.height = '1200px';
    filler.style.flex = '0 0 1200px';
    filler.style.border = '1px dashed #60a5fa';
    filler.setAttribute('data-testid', 'phase4-scroll-probe');
    left.appendChild(filler);
    const before = left.scrollTop;
    left.scrollTop = left.scrollHeight;
    const after = left.scrollTop;
    const computed = window.getComputedStyle(left);
    return {
      ok: computed.overflowY === 'auto' && after > before,
      before,
      after,
      clientHeight: left.clientHeight,
      scrollHeight: left.scrollHeight,
      overflowY: computed.overflowY,
    };
  });
  check('Project left column can scroll when Aces/workers overflow', scrollProbe.ok, JSON.stringify(scrollProbe));
  await screenshot(page, 'project-left-column-after-scroll');

  const report = {
    outDir,
    baseUrl,
    apiUrl,
    codexCommand,
    project,
    towerSession,
    leaderId,
    leaderSessionId,
    aceSessionId,
    roleFiles: { towerRoleFile, leaderRoleFile, aceRoleFile },
    checks,
    events,
    consoleMessages,
    failedRequests,
  };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`REPORT ${path.join(outDir, 'report.json')}`);
} catch (err) {
  checks.push({ name: 'Phase 4 Playwright validation completed without throw', ok: false, detail: err?.stack || err?.message || String(err) });
  await screenshot(page, 'failure');
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify({ outDir, checks, events, consoleMessages, failedRequests, project, towerSession, leaderId, leaderSessionId, aceSessionId }, null, 2));
  process.exitCode = 1;
} finally {
  await browser.close();
}

if (checks.some((entry) => !entry.ok)) {
  process.exitCode = 1;
}
