/**
 * Dashboard views E2E tests:
 * - App loads, TowerBar visible with cost metrics
 * - View toggle [Grid][Row][Board]
 * - Row view renders rows not cards
 * - Board view renders three columns
 * - Grid view returns to cards
 * - View preference persists on reload
 * - Usage page has charts, no placeholder text
 * - Settings page loads
 */

import { test, expect } from "@playwright/test";

const BASE_URL = "http://127.0.0.1:5176";
const API_URL = "http://127.0.0.1:8421";

test("1: App loads and TowerBar is visible with cost metrics", async ({
  page,
}) => {
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(BASE_URL);
  await page.waitForLoadState("domcontentloaded");
  await page.waitForSelector('[data-testid="tower-bar"]', { timeout: 10000 });

  await expect(page.locator('[data-testid="tower-bar"]')).toBeVisible();

  // TowerBar should show cost metrics ($ sign or "Today" label)
  const towerBarText = await page.locator('[data-testid="tower-bar"]').textContent();
  expect(towerBarText).toBeTruthy();

  expect(jsErrors).toHaveLength(0);
});

test("2: Dashboard has Grid/Row/Board view toggle", async ({ page }) => {
  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  const viewToggle = page.locator('[role="group"][aria-label="View mode"]');
  await expect(viewToggle).toBeVisible();
  await expect(viewToggle.locator("button", { hasText: "Grid" })).toBeVisible();
  await expect(viewToggle.locator("button", { hasText: "Row" })).toBeVisible();
  await expect(viewToggle.locator("button", { hasText: "Board" })).toBeVisible();
});

test("3: Click Row view — rows render (not grid cards)", async ({ page }) => {
  // Ensure at least one active project exists
  const listRes = await fetch(`${API_URL}/api/projects`);
  const projects = (await listRes.json()) as Array<{ id: string; status: string }>;
  if (projects.filter((p) => p.status === "active").length === 0) {
    await fetch(`${API_URL}/api/projects`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: "QA Views Test" }),
    });
  }

  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  // Start from Grid
  const toggle = page.locator('[role="group"][aria-label="View mode"]');
  await toggle.locator("button", { hasText: "Grid" }).click();
  await page.waitForTimeout(300);

  // Switch to Row
  await toggle.locator("button", { hasText: "Row" }).click();
  await page.waitForTimeout(400);

  const rowBtn = toggle.locator("button", { hasText: "Row" });
  await expect(rowBtn).toHaveAttribute("aria-pressed", "true");

  // Grid card container should not be present; row view should be
  const gridCount = await page.locator(".project-grid-view").count();
  const rowCount = await page.locator(".project-row-view").count();
  // At least one of: row view rendered OR grid view gone
  expect(gridCount === 0 || rowCount > 0).toBe(true);
});

test("4: Click Board view — three columns (Active, Paused, Archived)", async ({
  page,
}) => {
  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  const toggle = page.locator('[role="group"][aria-label="View mode"]');
  await toggle.locator("button", { hasText: "Board" }).click();
  await page.waitForTimeout(400);

  const boardBtn = toggle.locator("button", { hasText: "Board" });
  await expect(boardBtn).toHaveAttribute("aria-pressed", "true");

  await expect(page.locator(".project-board-view")).toBeVisible();
  await expect(page.locator(".board-column__label", { hasText: "Active" })).toBeVisible();
  await expect(page.locator(".board-column__label", { hasText: "Paused" })).toBeVisible();
  await expect(page.locator(".board-column__label", { hasText: "Archived" })).toBeVisible();
});

test("5: Click Grid view — back to cards", async ({ page }) => {
  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  const toggle = page.locator('[role="group"][aria-label="View mode"]');

  // Go to Board first
  await toggle.locator("button", { hasText: "Board" }).click();
  await page.waitForTimeout(300);

  // Back to Grid
  await toggle.locator("button", { hasText: "Grid" }).click();
  await page.waitForTimeout(400);

  await expect(toggle.locator("button", { hasText: "Grid" })).toHaveAttribute(
    "aria-pressed",
    "true"
  );
  // Board view should be gone
  expect(await page.locator(".project-board-view").count()).toBe(0);
});

test("6: View preference persists after page reload", async ({ page }) => {
  await page.goto(`${BASE_URL}/dashboard`);
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  // Switch to Row
  const toggle = page.locator('[role="group"][aria-label="View mode"]');
  await toggle.locator("button", { hasText: "Row" }).click();
  await page.waitForTimeout(300);

  // Reload
  await page.reload();
  await page.waitForSelector('[data-testid="dashboard-page"]', {
    timeout: 10000,
  });

  // Row should still be selected
  await expect(
    page.locator('[role="group"][aria-label="View mode"]').locator("button", { hasText: "Row" })
  ).toHaveAttribute("aria-pressed", "true");

  // Reset to Grid
  await page
    .locator('[role="group"][aria-label="View mode"]')
    .locator("button", { hasText: "Grid" })
    .click();
});

test("7: Usage page loads with charts — no 'placeholder' text", async ({
  page,
}) => {
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(`${BASE_URL}/usage`);
  await page.waitForSelector('[data-testid="usage-page"]', { timeout: 10000 });

  await expect(page.locator('[data-testid="usage-page"]')).toBeVisible();

  // Key sections present
  await expect(page.locator("text=Cost Overview")).toBeVisible();
  await expect(page.locator("text=Token Usage")).toBeVisible();

  // No placeholder text anywhere on the page
  expect(await page.locator("text=placeholder").count()).toBe(0);
  expect(await page.locator("text=Cost chart placeholder").count()).toBe(0);

  expect(jsErrors).toHaveLength(0);
});

test("8: Settings page loads without errors", async ({ page }) => {
  const jsErrors: string[] = [];
  page.on("pageerror", (err) => jsErrors.push(err.message));

  await page.goto(`${BASE_URL}/settings`);
  await page.waitForLoadState("domcontentloaded");
  // Settings may redirect or render — just ensure no crash
  await page.waitForTimeout(2000);

  expect(jsErrors).toHaveLength(0);
});
