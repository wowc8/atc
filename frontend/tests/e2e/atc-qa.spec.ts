import { test, expect, Page } from "@playwright/test";

const BASE_URL = "http://127.0.0.1:5176";
const API_URL = "http://127.0.0.1:8421";

// Helper: fetch API directly in tests
async function apiGet(url: string): Promise<unknown> {
  const res = await fetch(`${API_URL}${url}`);
  if (!res.ok) throw new Error(`GET ${url} failed: ${res.status}`);
  return res.json();
}

async function apiPost(url: string, body: unknown): Promise<unknown> {
  const res = await fetch(`${API_URL}${url}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`POST ${url} failed: ${res.status}`);
  return res.json();
}

async function apiPut(url: string, body: unknown): Promise<unknown> {
  const res = await fetch(`${API_URL}${url}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`PUT ${url} failed: ${res.status}`);
  return res.json();
}

// Get or create the test project
let _testProjectId: string | null = null;
async function getTestProjectId(): Promise<string> {
  if (_testProjectId) return _testProjectId;
  const projects = (await apiGet("/api/projects")) as Array<{
    id: string;
    name: string;
    status: string;
  }>;
  const existing = projects.find((p) => p.name === "Test Project" && p.status === "active");
  if (existing) {
    _testProjectId = existing.id;
    return _testProjectId;
  }
  const created = (await apiPost("/api/projects", {
    name: "Test Project",
    description: "QA test",
  })) as { id: string };
  _testProjectId = created.id;
  return _testProjectId;
}

// ============================================================================
// Test 1: App loads
// ============================================================================
test("Test 1: App loads without JS errors", async ({ page }) => {
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(BASE_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForSelector('[data-testid="tower-bar"]', { timeout: 10000 });

  // No JS errors
  expect(jsErrors).toHaveLength(0);

  // TowerBar is visible
  await expect(page.locator('[data-testid="tower-bar"]')).toBeVisible();

  // Cost and token metrics in TowerBar
  const costSummary = page.locator('[data-testid="cost-summary"]');
  await expect(costSummary).toBeVisible();
  await expect(costSummary).toContainText("$");

  const tokenSummary = page.locator('[data-testid="token-summary"]');
  await expect(tokenSummary).toBeVisible();
  await expect(tokenSummary).toContainText("tokens");

  // Navigation buttons present (use specific nav selector)
  await expect(page.locator(".tower-bar__nav-item", { hasText: "Dashboard" })).toBeVisible();
  await expect(page.locator(".tower-bar__nav-item", { hasText: "Context" })).toBeVisible();
  await expect(page.locator(".tower-bar__nav-item", { hasText: "Usage" })).toBeVisible();
});

// ============================================================================
// Test 2: Dashboard page
// ============================================================================
test("Test 2: Dashboard page renders correctly", async ({ page }) => {
  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForSelector('[data-testid="dashboard-page"]', { timeout: 10000 });

  // Dashboard page renders
  await expect(page.locator('[data-testid="dashboard-page"]')).toBeVisible();

  // Stats cards are visible (using specific heading selectors)
  await expect(page.locator(".dashboard__card-title", { hasText: "Cost" })).toBeVisible();
  await expect(page.locator(".dashboard__card-title", { hasText: "Tokens" })).toBeVisible();
  await expect(page.locator(".dashboard__card-title", { hasText: "Sessions" })).toBeVisible();

  // No stub divs like "Cost chart placeholder"
  const placeholderText = await page.locator("text=Cost chart placeholder").count();
  expect(placeholderText).toBe(0);

  // Project list section exists
  await expect(page.locator("h2", { hasText: "Projects" })).toBeVisible();

  // New Project button works
  const newProjectBtn = page.locator("text=+ New Project");
  await expect(newProjectBtn).toBeVisible();
});

// ============================================================================
// Test 3: Usage page
// ============================================================================
test("Test 3: Usage page renders all chart sections", async ({ page }) => {
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(`${BASE_URL}/usage`);
  // Use domcontentloaded instead of networkidle because WebSocket keeps connection alive
  await page.waitForLoadState("domcontentloaded");
  // Wait for the page content to render
  await page.waitForSelector('[data-testid="usage-page"]', { timeout: 10000 });

  // No JS errors on usage page
  expect(jsErrors).toHaveLength(0);

  // data-testid attribute
  await expect(page.locator('[data-testid="usage-page"]')).toBeVisible();

  // All four sections render
  await expect(page.locator("text=Cost Overview")).toBeVisible();
  await expect(page.locator("text=Token Usage")).toBeVisible();
  await expect(page.locator("text=CPU / RAM")).toBeVisible();
  await expect(page.locator("text=Budget Utilization")).toBeVisible();

  // No placeholder text from old stub code
  const oldPlaceholder = await page.locator("text=Cost chart placeholder").count();
  expect(oldPlaceholder).toBe(0);

  // Period selector buttons exist
  const sevenDay = page.locator(".usage-page__period-btn", { hasText: "7d" });
  const thirtyDay = page.locator(".usage-page__period-btn", { hasText: "30d" });
  const ninetyDay = page.locator(".usage-page__period-btn", { hasText: "90d" });

  await expect(sevenDay).toBeVisible();
  await expect(thirtyDay).toBeVisible();
  await expect(ninetyDay).toBeVisible();

  // Period selector is clickable
  await thirtyDay.click();
  await expect(thirtyDay).toHaveClass(/usage-page__period-btn--active/);

  await sevenDay.click();
  await expect(sevenDay).toHaveClass(/usage-page__period-btn--active/);
});

// ============================================================================
// Test 4: Budget API
// ============================================================================
test("Test 4: Budget API CRUD operations", async ({ page }) => {
  const projectId = await getTestProjectId();

  // GET budget - should return budget object
  const budget = (await apiGet(`/api/projects/${projectId}/budget`)) as {
    project_id: string;
    daily_token_limit: number | null;
    monthly_cost_limit: number | null;
    warn_threshold: number;
    current_status: string;
    updated_at: string;
  };

  expect(budget).toHaveProperty("project_id");
  expect(budget.project_id).toBe(projectId);
  expect(budget).toHaveProperty("daily_token_limit");
  expect(budget).toHaveProperty("monthly_cost_limit");
  expect(budget).toHaveProperty("warn_threshold");
  expect(budget).toHaveProperty("current_status");
  expect(budget).toHaveProperty("updated_at");

  // PUT budget - update limits
  const updated = (await apiPut(`/api/projects/${projectId}/budget`, {
    daily_token_limit: 5000,
    monthly_cost_limit: 25.0,
    warn_threshold: 0.9,
  })) as {
    project_id: string;
    daily_token_limit: number;
    monthly_cost_limit: number;
    warn_threshold: number;
  };

  expect(updated.daily_token_limit).toBe(5000);
  expect(updated.monthly_cost_limit).toBe(25.0);
  expect(updated.warn_threshold).toBe(0.9);

  // GET again to verify saved
  const verified = (await apiGet(`/api/projects/${projectId}/budget`)) as {
    daily_token_limit: number;
    monthly_cost_limit: number;
    warn_threshold: number;
  };

  expect(verified.daily_token_limit).toBe(5000);
  expect(verified.monthly_cost_limit).toBe(25.0);
  expect(verified.warn_threshold).toBe(0.9);
});

// ============================================================================
// Test 5: Project page tabs
// ============================================================================
test("Test 5: Project page tabs render correctly", async ({ page }) => {
  const projectId = await getTestProjectId();
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(`${BASE_URL}/projects/${projectId}`);
  await page.waitForLoadState("domcontentloaded");
  // Wait for project data to load and component to render
  await page.waitForSelector('[data-testid="project-view"]', { timeout: 10000 });
  // Wait for the leader console tabs to appear
  await page.waitForSelector(".leader-console__tab", { timeout: 10000 });

  // No JS errors
  expect(jsErrors).toHaveLength(0);

  // Tabs are visible
  await expect(page.locator(".leader-console__tab", { hasText: "Tasks" })).toBeVisible();
  await expect(page.locator(".leader-console__tab", { hasText: "GitHub" })).toBeVisible();
  await expect(page.locator(".leader-console__tab", { hasText: "Budget" })).toBeVisible();

  // Click Budget tab
  await page.locator(".leader-console__tab", { hasText: "Budget" }).click();
  // BudgetPanel should render (not blank)
  await page.waitForTimeout(500);
  const tabContent = page.locator(".leader-console__tab-content");
  await expect(tabContent).toBeVisible();
  // Budget panel shows budget-related content
  const budgetContent = await tabContent.textContent();
  expect(budgetContent).not.toBe("");

  // Click GitHub tab
  await page.locator(".leader-console__tab", { hasText: "GitHub" }).click();
  await page.waitForTimeout(500);
  const githubContent = await tabContent.textContent();
  expect(githubContent).not.toBe("");

  // Click Tasks tab
  await page.locator(".leader-console__tab", { hasText: "Tasks" }).click();
  await page.waitForTimeout(500);
  const tasksContent = await tabContent.textContent();
  expect(tasksContent).not.toBe("");
});

// ============================================================================
// Test 6: API endpoints directly
// ============================================================================
test("Test 6: API endpoints return correct structure", async ({ page }) => {
  // GET /api/usage/summary
  const summary = (await apiGet("/api/usage/summary")) as {
    today_cost: number;
    month_cost: number;
    today_tokens: number;
    month_tokens: number;
  };
  expect(summary).toHaveProperty("today_cost");
  expect(summary).toHaveProperty("month_cost");
  expect(summary).toHaveProperty("today_tokens");
  expect(summary).toHaveProperty("month_tokens");
  expect(typeof summary.today_cost).toBe("number");
  expect(typeof summary.today_tokens).toBe("number");

  // GET /api/usage/cost?period=7d - should return array
  const costData = (await apiGet("/api/usage/cost?period=7d")) as unknown[];
  expect(Array.isArray(costData)).toBe(true);

  // GET /api/usage/tokens?period=7d - should return array
  const tokenData = (await apiGet("/api/usage/tokens?period=7d")) as unknown[];
  expect(Array.isArray(tokenData)).toBe(true);

  // GET /api/usage/resources - should return array
  const resourceData = (await apiGet("/api/usage/resources")) as unknown[];
  expect(Array.isArray(resourceData)).toBe(true);

  // GET /api/usage/github - should return array
  const githubData = (await apiGet("/api/usage/github")) as unknown[];
  expect(Array.isArray(githubData)).toBe(true);

  // GET /api/projects/{id}/github/prs - should return array (not 500)
  const projectId = await getTestProjectId();
  const prs = (await apiGet(`/api/projects/${projectId}/github/prs`)) as unknown[];
  expect(Array.isArray(prs)).toBe(true);

  // POST /api/projects/{id}/github/sync - project has no repo, expects 422
  let syncStatus = 0;
  try {
    await fetch(`${API_URL}/api/projects/${projectId}/github/sync`, { method: "POST" });
  } catch {
    // network error ok
  }
  const syncRes = await fetch(`${API_URL}/api/projects/${projectId}/github/sync`, { method: "POST" });
  syncStatus = syncRes.status;
  // Should be 422 (no github_repo configured) not 500
  expect(syncStatus).toBe(422);
});

// ============================================================================
// Test 7: TowerBar metrics accuracy
// ============================================================================
test("Test 7: TowerBar shows correct initial metrics", async ({ page }) => {
  await page.goto(BASE_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForSelector('[data-testid="tower-bar"]', { timeout: 10000 });

  const costEl = page.locator('[data-testid="cost-summary"]');
  const tokenEl = page.locator('[data-testid="token-summary"]');
  const projectCountEl = page.locator('[data-testid="project-count"]');

  await expect(costEl).toBeVisible();
  await expect(tokenEl).toBeVisible();
  await expect(projectCountEl).toBeVisible();

  // Should show $0.00 today (no usage yet)
  const costText = await costEl.textContent();
  expect(costText).toContain("$");

  const tokenText = await tokenEl.textContent();
  expect(tokenText).toContain("tokens");

  // Should show at least 1 project (Test Project)
  const projectText = await projectCountEl.textContent();
  expect(projectText).toMatch(/\d+ project/);
});
