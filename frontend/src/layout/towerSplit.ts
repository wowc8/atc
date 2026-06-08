export const SIDE_TOWER_WIDTH_KEY = "atc:layout:tower-width";
export const SIDE_TOWER_MIN_WIDTH = 320;
export const SIDE_TOWER_MAX_RATIO = 0.55;
export const SIDE_TOWER_DEFAULT_WIDTH = 520;

export function shouldShowTowerPanel(pathname: string) {
  return pathname === "/dashboard" || pathname.startsWith("/projects/");
}

export function isSideTowerRoute(pathname: string) {
  return shouldShowTowerPanel(pathname);
}

export function readStoredTowerWidth() {
  if (typeof window === "undefined") {
    return SIDE_TOWER_DEFAULT_WIDTH;
  }

  const stored = Number(window.localStorage.getItem(SIDE_TOWER_WIDTH_KEY));
  return Number.isFinite(stored) ? stored : SIDE_TOWER_DEFAULT_WIDTH;
}

export function clampTowerWidth(width: number, containerWidth: number) {
  const maxWidth = Math.max(
    SIDE_TOWER_MIN_WIDTH,
    Math.min(containerWidth * SIDE_TOWER_MAX_RATIO, containerWidth - 280),
  );

  return Math.min(Math.max(width, SIDE_TOWER_MIN_WIDTH), maxWidth);
}
