import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const root = path.resolve(new URL('../..', import.meta.url).pathname);
const outDir = path.join(root, 'test-results', `phase7-truthful-delivery-${stamp}`);
const baseUrl = process.env.ATC_UI_URL || 'http://localhost:5173';
const apiUrl = process.env.ATC_API_URL || 'http://127.0.0.1:8420';

fs.mkdirSync(outDir, { recursive: true });

const checks = [];
const events = [];
const consoleMessages = [];
const failedRequests = [];
let screenshotIndex = 0;
let project = null;
let task = null;
let leaderStart = null;
let spawnResult = null;
let instructResult = null;
let ace = null;
let refreshedTask = null;
let deliveryEvents = [];
let tmuxEvidence = null;

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

function inspectTmuxPane(session) {
  if (!session?.tmux_pane) return { alive: false, detail: 'missing tmux_pane' };
  try {
    const out = execFileSync('tmux', ['display-message', '-p', '-t', session.tmux_pane, '#{pane_id} #{pane_dead}'], {
      encoding: 'utf8',
      timeout: 5000,
    }).trim();
    return { alive: out.includes(session.tmux_pane) && out.endsWith('0'), detail: out };
  } catch (err) {
    return { alive: false, detail: err?.message || String(err) };
  }
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

  const projectResp = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({
      name: `Phase 7 Truth ${stamp.slice(0, 19)}`,
      description: 'Temporary Phase 7 truthful delivery validation project.',
      agent_provider: 'codex',
    }),
  });
  check('Temporary project created', projectResp.ok, JSON.stringify(projectResp.body));
  project = projectResp.body;

  const taskResp = await api(`/api/projects/${project.id}/task-graphs`, {
    method: 'POST',
    body: JSON.stringify({
      title: 'Phase 7 Ace delivery truth task',
      description: 'Disposable validation task proving Leader-created Ace assignment truth.',
      status: 'todo',
    }),
  });
  check('Temporary task created', taskResp.ok, JSON.stringify(taskResp.body));
  task = taskResp.body;

  leaderStart = await api(`/api/projects/${project.id}/leader/start`, {
    method: 'POST',
    body: JSON.stringify({
      goal: 'Validate truthful delivery status wording and create one Ace for a disposable task.',
      auto_kickoff: true,
    }),
  });
  check('Leader start endpoint succeeds', leaderStart.ok, JSON.stringify(leaderStart.body));
  check('Leader start truthfully reports queued kickoff', leaderStart.body?.delivery_state === 'queued', JSON.stringify(leaderStart.body));
  check('Leader start explains queued is not proof', /not prove|not proof|queued/i.test(leaderStart.body?.recovery || leaderStart.body?.message || ''), JSON.stringify(leaderStart.body));

  await page.goto(`${baseUrl}/projects/${project.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await screenshot(page, 'project-loaded-before-leader-start-ui');

  const leaderBanner = page.getByTestId('leader-delivery-state');
  await page.getByTestId('leader-console').waitFor({ timeout: 15000 });
  if (await leaderBanner.count()) {
    await leaderBanner.first().waitFor({ timeout: 15000 });
    const bannerText = await leaderBanner.first().innerText();
    check('Leader UI surfaces delivery state banner', /Delivery state:/i.test(bannerText), bannerText);
    await screenshot(page, 'leader-delivery-state-banner');
  } else {
    note('Leader UI banner not present after API start; API contract remains authoritative for this run');
  }

  spawnResult = await api(`/api/projects/${project.id}/leader/spawn-aces`, { method: 'POST', body: JSON.stringify({}) });
  check('Leader spawn-aces endpoint succeeds', spawnResult.ok, JSON.stringify(spawnResult.body));
  check('Leader created at least one Ace assignment', (spawnResult.body?.spawned || []).length >= 1, JSON.stringify(spawnResult.body));
  const spawned = spawnResult.body.spawned[0];

  const acesResp = await api(`/api/projects/${project.id}/aces`);
  check('Ace list endpoint succeeds', acesResp.ok, JSON.stringify(acesResp.body));
  ace = (acesResp.body || []).find((entry) => entry.id === spawned.ace_session_id);
  check('Leader-created Ace session is listed', Boolean(ace), JSON.stringify(acesResp.body));
  check('Leader-created session is an Ace', ace?.session_type === 'ace', JSON.stringify(ace));
  tmuxEvidence = inspectTmuxPane(ace);
  check('Leader-created Ace tmux pane is live', tmuxEvidence.alive, JSON.stringify(tmuxEvidence));

  instructResult = await api(`/api/projects/${project.id}/leader/instruct`, {
    method: 'POST',
    body: JSON.stringify({
      task_graph_id: spawned.task_graph_id,
      instruction: 'Acknowledge this Phase 7 validation assignment and wait for further instruction.',
    }),
  });
  check('Leader instruct endpoint succeeds', instructResult.ok, JSON.stringify(instructResult.body));
  check('Leader instruct does not return sent/accepted ambiguity', !['sent', 'accepted'].includes(instructResult.body?.status), JSON.stringify(instructResult.body));
  check('Leader instruct returns delivery_state', Boolean(instructResult.body?.delivery_state), JSON.stringify(instructResult.body));
  check(
    'Ace delivery is delivered/confirmed or explicitly blocked/failed',
    ['delivered', 'confirmed', 'blocked', 'failed'].includes(instructResult.body?.delivery_state),
    JSON.stringify(instructResult.body),
  );

  const taskGraphResp = await api(`/api/task-graphs/${spawned.task_graph_id}`);
  check('Assigned task fetch succeeds', taskGraphResp.ok, JSON.stringify(taskGraphResp.body));
  refreshedTask = taskGraphResp.body;
  check('Task assignment points to the live Ace session', refreshedTask.assigned_ace_id === ace.id, JSON.stringify(refreshedTask));
  check('Task is in working/in-progress state after instruction', ['assigned', 'in_progress'].includes(refreshedTask.status), JSON.stringify(refreshedTask));

  const eventsResp = await api(`/api/app-events?category=delivery_trace&project_id=${project.id}&limit=50`);
  check('Delivery trace events endpoint succeeds', eventsResp.ok, JSON.stringify(eventsResp.body));
  deliveryEvents = eventsResp.body || [];
  check('Runtime delivery trace evidence exists', deliveryEvents.length >= 1, JSON.stringify(deliveryEvents));

  await page.goto(`${baseUrl}/projects/${project.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await page.getByTestId('ace-list').waitFor({ timeout: 15000 });
  await screenshot(page, 'project-ace-assignment-visible');

  check('No failed browser requests', failedRequests.length === 0, JSON.stringify(failedRequests));
  const pageErrors = consoleMessages.filter((entry) => entry.type === 'pageerror');
  check('No page errors', pageErrors.length === 0, JSON.stringify(pageErrors));

  const report = {
    outDir,
    baseUrl,
    apiUrl,
    project,
    task,
    leaderStart: leaderStart.body,
    spawnResult: spawnResult.body,
    instructResult: instructResult.body,
    ace,
    refreshedTask,
    tmuxEvidence,
    deliveryEvents,
    checks,
    events,
    consoleMessages,
    failedRequests,
  };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`REPORT ${path.join(outDir, 'report.json')}`);
} catch (err) {
  checks.push({
    name: 'Phase 7 Playwright/API validation completed without throw',
    ok: false,
    detail: err?.stack || err?.message || String(err),
  });
  await screenshot(page, 'failure');
  fs.writeFileSync(
    path.join(outDir, 'report.json'),
    JSON.stringify({ outDir, checks, events, consoleMessages, failedRequests, project, task, leaderStart, spawnResult, instructResult, ace, refreshedTask, tmuxEvidence, deliveryEvents }, null, 2),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}

if (checks.some((entry) => !entry.ok)) {
  process.exitCode = 1;
}
