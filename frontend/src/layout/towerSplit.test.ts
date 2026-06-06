import { describe, expect, it } from "vitest";
import { clampTowerWidth, isSideTowerRoute, SIDE_TOWER_DEFAULT_WIDTH, SIDE_TOWER_MIN_WIDTH } from "./towerSplit";

describe("towerSplit", () => {
  it("matches the shell routes that should show the side tower", () => {
    expect(isSideTowerRoute("/dashboard")).toBe(true);
    expect(isSideTowerRoute("/projects/abc")).toBe(true);
    expect(isSideTowerRoute("/usage")).toBe(false);
  });

  it("clamps the width to the minimum", () => {
    expect(clampTowerWidth(200, 1400)).toBe(SIDE_TOWER_MIN_WIDTH);
  });

  it("clamps the width to the computed maximum", () => {
    expect(clampTowerWidth(900, 1000)).toBe(550);
    expect(clampTowerWidth(900, 700)).toBeCloseTo(385);
  });

  it("keeps widths already inside the allowed range", () => {
    expect(clampTowerWidth(SIDE_TOWER_DEFAULT_WIDTH, 1400)).toBe(SIDE_TOWER_DEFAULT_WIDTH);
  });
});
