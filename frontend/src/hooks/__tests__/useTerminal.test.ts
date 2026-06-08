import { describe, expect, it } from "vitest";
import { isTerminalViewportAtBottom } from "../useTerminal";

function terminalViewport(baseY: number, viewportY: number) {
  return {
    buffer: {
      active: {
        baseY,
        viewportY,
      },
    },
  };
}

describe("useTerminal viewport behavior", () => {
  it("treats the terminal as sticky when it is at the bottom", () => {
    expect(isTerminalViewportAtBottom(terminalViewport(120, 120))).toBe(true);
  });

  it("keeps the terminal sticky within a small tolerance", () => {
    expect(isTerminalViewportAtBottom(terminalViewport(120, 119))).toBe(true);
  });

  it("detects when the operator has scrolled away from the bottom", () => {
    expect(isTerminalViewportAtBottom(terminalViewport(120, 100))).toBe(false);
  });
});
