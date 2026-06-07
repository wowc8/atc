import { chromium } from '@playwright/test';
import fs from 'fs';
import path from 'path';

const stamp = new Date().toISOString().replace(/[:.]/g, '-');
const outDir = path.resolve('../screenshots/tower-codex-flow-' + stamp);
fs.mkdirSync(outDir, { recursive: true });
const BACKEND = 'http://127.0.0.1:8420';
const codexCommand = '/Users/mcole_studio/.local/bin/codex --dangerously-bypass-approvals-and-sandbox';
const controlName = `ATC Codex Control ${stamp.slice(0, 19)}`;
const generatedName = `Tower Codex Generated ${stamp.slice(0, 19)}`;
const generatedPath = `/tmp/atc-tower-generated-${stamp}`;
const events = [];
const errors = [];
const requests = [];
let screenshotIndex = 0;

function log(name, detail = '') { events.push({ t: new Date().toISOString(), name, detail }); console.log(`${name}${detail ? ' — ' + detail : ''}`); }
async function screenshot(page, name) { const file = path.join(outDir, `${String(screenshotIndex++).padStart(2, '0')}-${name}.png`); await page.screenshot({ path: file, fullPage: true }); log('SCREENSHOT', file); }
async function api(route, options = {}) { const res = await fetch(`${BACKEND}${route}`, { headers: { 'Content-Type': 'application/json' }, ...options }); const text = await res.text(); let body; try { body = text ? JSON.parse(text) : null; } catch { body = text; } return { ok: res.ok, status: res.status, body }; }
async function waitFor(name, fn, timeoutMs = 60000, intervalMs = 2000) { const start = Date.now(); let last; while (Date.now() - start < timeoutMs) { last = await fn(); if (last?.ok) { log(name, JSON.stringify(last.value ?? last)); return last.value ?? last; } await new Promise(r => setTimeout(r, intervalMs)); } throw new Error(`${name} timed out; last=${JSON.stringify(last)}`); }

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1100 }, deviceScaleFactor: 1 });
page.on('console', msg => { if (['error', 'warning'].includes(msg.type())) errors.push({ type: msg.type(), text: msg.text() }); });
page.on('pageerror', err => errors.push({ type: 'pageerror', text: err.message }));
page.on('response', res => { if (res.status() >= 400) requests.push({ url: res.url(), status: res.status() }); });

let controlProject = null;
let goalResponse = null;
let final = {};
try {
  await page.goto('http://localhost:5173/dashboard', { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByTestId('tower-bar').waitFor({ timeout: 15000 });
  await screenshot(page, 'dashboard-before-settings');

  await page.getByTestId('settings-button').click();
  await page.getByTestId('settings-pane').waitFor({ timeout: 10000 });
  await page.locator('#provider-default').selectOption('codex');
  await page.locator('#provider-codex-command').fill(codexCommand);
  await page.locator('#provider-codex-command').blur();
  await page.waitForTimeout(1200);
  const providerStatus = await api('/api/settings/agent-provider');
  log('Provider config after settings cog', JSON.stringify(providerStatus.body));
  await screenshot(page, 'settings-codex');
  await page.getByTestId('close-settings-pane').click();

  await page.getByRole('button', { name: '+ New Project' }).click();
  await page.locator('#project-name').fill(controlName);
  await page.locator('#project-desc').fill('Temporary control project for Codex/Tower smoke testing.');
  await page.locator('#project-repo').fill('/Users/mcole_studio/Repository/atc');
  await page.locator('#project-provider').selectOption('codex');
  const projectResponsePromise = page.waitForResponse(resp => resp.url().includes('/api/projects') && resp.request().method() === 'POST');
  await page.getByRole('button', { name: 'Create Project' }).click();
  const projectResponse = await projectResponsePromise;
  controlProject = await projectResponse.json();
  log('Control project created', JSON.stringify({ id: controlProject.id, name: controlProject.name, provider: controlProject.agent_provider }));

  await page.goto(`http://localhost:5173/projects/${controlProject.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.getByTestId('tower-panel').waitFor({ timeout: 15000 });
  await page.getByTestId('tower-panel-toggle').click().catch(() => {});
  await screenshot(page, 'project-view-tower-panel');

  // TowerPanel auto-starts when a project route is active. If not, click Start.
  const start = page.getByTestId('tower-panel-start');
  if (await start.isVisible().catch(() => false)) { await start.click(); log('Clicked Tower start'); }
  const towerStatus = await waitFor('Tower session running', async () => {
    const res = await api('/api/tower/status');
    const b = res.body;
    return { ok: res.ok && b?.current_session_id && ['planning', 'managing'].includes(b.state), value: b };
  }, 90000, 3000);
  await screenshot(page, 'tower-started');

  const goal = `Create a new ATC project record named "${generatedName}" with description "Created by Tower Codex smoke test" and repo path "${generatedPath}". Decompose this goal into tasks using POST /api/projects/${controlProject.id}/leader/decompose, spawn Aces for ready tasks, monitor progress, and report completion. Keep any filesystem writes inside ${generatedPath}.`;
  goalResponse = await api('/api/tower/goal', { method: 'POST', body: JSON.stringify({ project_id: controlProject.id, goal }) });
  log('Submitted Tower goal', JSON.stringify(goalResponse.body));
  await screenshot(page, 'goal-submitted');

  await waitFor('Leader session created', async () => {
    const res = await api('/api/tower/status');
    return { ok: res.ok && !!res.body?.leader_session_id, value: res.body };
  }, 90000, 3000);

  const activity = await waitFor('Leader/Ace/generated-project activity', async () => {
    const [sessions, projects, status] = await Promise.all([
      api('/api/orchestration/sessions'),
      api('/api/projects'),
      api('/api/tower/status'),
    ]);
    const sessionList = Array.isArray(sessions.body) ? sessions.body : [];
    const relevant = sessionList.filter(s => s.project_id === controlProject.id);
    const leaderCount = relevant.filter(s => s.role === 'leader' || s.raw_session_type === 'manager').length;
    const aceCount = relevant.filter(s => s.role === 'ace' || s.raw_session_type === 'ace').length;
    const generated = Array.isArray(projects.body) ? projects.body.find(p => p.name === generatedName) : null;
    return { ok: leaderCount > 0 && (aceCount > 0 || generated || status.body?.output_line_count > 0), value: { leaderCount, aceCount, generatedProjectId: generated?.id, status: status.body } };
  }, 300000, 5000);
  await page.goto(`http://localhost:5173/projects/${controlProject.id}`, { waitUntil: 'networkidle', timeout: 30000 });
  await screenshot(page, 'activity-observed');
  final.activity = activity;
} catch (err) {
  log('ERROR', err?.stack || err?.message || String(err));
  await screenshot(page, 'failure');
  process.exitCode = 1;
} finally {
  final = { ...final, outDir, codexCommand, controlProject, goalResponse, events, errors, requests, towerStatus: await api('/api/tower/status').catch(e => ({ ok: false, body: String(e) })), sessions: await api('/api/orchestration/sessions').catch(e => ({ ok: false, body: String(e) })), projects: await api('/api/projects').catch(e => ({ ok: false, body: String(e) })) };
  fs.writeFileSync(path.join(outDir, 'report.json'), JSON.stringify(final, null, 2));
  console.log('REPORT ' + path.join(outDir, 'report.json'));
  await browser.close();
}
