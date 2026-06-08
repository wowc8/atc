import { chromium } from '../../frontend/node_modules/@playwright/test/index.mjs';
import fs from 'node:fs';
import path from 'node:path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const root = path.resolve(new URL('../..', import.meta.url).pathname);
const outDir = path.join(root, 'test-results', `phase6-reconcile-${stamp}`);
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
let dryRun = null;
let repairRun = null;
let repairedTask = null;
let auditEvents = [];

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

  const projectResp = await api('/api/projects', {
    method: 'POST',
    body: JSON.stringify({
      name: `Phase 6 Reconcile ${stamp.slice(0, 19)}`,
      description: 'Temporary Phase 6 validation project.',
      agent_provider: 'codex',
    }),
  });
  check('Temporary project created', projectResp.ok, JSON.stringify(projectResp.body));
  project = projectResp.body;

  const taskResp = await api(`/api/projects/${project.id}/task-graphs`, {
    method: 'POST',
    body: JSON.stringify({
      title: 'Phase 6 orphaned task validation',
      description: 'Assigned to a missing Ace so reconcile can detect and safely repair it.',
      status: 'assigned',
      assigned_ace_id: `missing-ace-${stamp}`,
    }),
  });
  check('Temporary orphaned task created', taskResp.ok, JSON.stringify(taskResp.body));
  task = taskResp.body;

  dryRun = await api('/api/orchestration/reconcile', {
    method: 'POST',
    body: JSON.stringify({ repair: false }),
  });
  check('Reconcile dry-run endpoint succeeds', dryRun.ok, JSON.stringify(dryRun.body));
  const dryFinding = dryRun.body?.findings?.find((entry) => entry.task_graph_id === task.id);
  check('Dry-run reports orphaned task finding', Boolean(dryFinding), JSON.stringify(dryRun.body));
  check('Dry-run does not mutate state', dryFinding?.repair_status === 'not_requested', JSON.stringify(dryFinding));
  check(
    'Dry-run recommends reset for reassignment',
    dryFinding?.recommended_action === 'reset_task_for_reassignment',
    JSON.stringify(dryFinding),
  );

  const beforeRepair = await api(`/api/task-graphs/${task.id}`);
  check(
    'Task remains assigned before repair',
    beforeRepair.body?.status === 'assigned' && beforeRepair.body?.assigned_ace_id,
    JSON.stringify(beforeRepair.body),
  );

  repairRun = await api('/api/orchestration/reconcile', {
    method: 'POST',
    body: JSON.stringify({ repair: true }),
  });
  check('Reconcile repair endpoint succeeds', repairRun.ok, JSON.stringify(repairRun.body));
  const repairFinding = repairRun.body?.findings?.find((entry) => entry.task_graph_id === task.id);
  check('Repair reports the same orphaned task', Boolean(repairFinding), JSON.stringify(repairRun.body));
  check('Repair status is applied', repairFinding?.repair_status === 'applied', JSON.stringify(repairFinding));

  const repairedTaskResp = await api(`/api/task-graphs/${task.id}`);
  check('Repaired task fetch succeeds', repairedTaskResp.ok, JSON.stringify(repairedTaskResp.body));
  repairedTask = repairedTaskResp.body;
  check(
    'Repair resets task to todo and clears Ace assignment',
    repairedTask.status === 'todo' && repairedTask.assigned_ace_id === null,
    JSON.stringify(repairedTask),
  );

  const eventsResp = await api(`/api/app-events?category=reconcile&project_id=${project.id}&limit=20`);
  check('Reconcile audit events are queryable', eventsResp.ok, JSON.stringify(eventsResp.body));
  auditEvents = eventsResp.body || [];
  check('Reconcile repair wrote an audit event', auditEvents.length >= 1, JSON.stringify(auditEvents));

  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await screenshot(page, 'dashboard-phase6-project-visible');

  await page.goto(`${baseUrl}/projects/${project.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByText(project.name).first().waitFor({ timeout: 15000 });
  await screenshot(page, 'project-phase6-reconcile-visible');

  check('No failed browser requests', failedRequests.length === 0, JSON.stringify(failedRequests));
  const pageErrors = consoleMessages.filter((entry) => entry.type === 'pageerror');
  check('No page errors', pageErrors.length === 0, JSON.stringify(pageErrors));

  const report = {
    outDir,
    baseUrl,
    apiUrl,
    project,
    task,
    dryRun: dryRun.body,
    repairRun: repairRun.body,
    repairedTask,
    auditEvents,
    checks,
    events,
    consoleMessages,
    failedRequests,
  };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(report, null, 2));
  console.log(`REPORT ${path.join(outDir, 'report.json')}`);
} catch (err) {
  checks.push({
    name: 'Phase 6 Playwright/API validation completed without throw',
    ok: false,
    detail: err?.stack || err?.message || String(err),
  });
  await screenshot(page, 'failure');
  fs.writeFileSync(
    path.join(outDir, 'report.json'),
    JSON.stringify(
      { outDir, checks, events, consoleMessages, failedRequests, project, task, dryRun, repairRun, repairedTask },
      null,
      2,
    ),
  );
  process.exitCode = 1;
} finally {
  await browser.close();
}

if (checks.some((entry) => !entry.ok)) {
  process.exitCode = 1;
}
