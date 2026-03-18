import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, it, expect, vi } from "vitest";
import UpdateBanner from "../UpdateBanner";

describe("UpdateBanner", () => {
  const defaultProps = {
    updateInfo: { version: "1.2.0" },
    status: "available" as const,
    progress: 0,
    onInstall: vi.fn(),
    onDismiss: vi.fn(),
  };

  it("shows version when update is available", () => {
    render(<UpdateBanner {...defaultProps} />);
    expect(screen.getByText(/v1\.2\.0/)).toBeInTheDocument();
  });

  it("shows Install & Restart button when available", () => {
    render(<UpdateBanner {...defaultProps} />);
    expect(screen.getByTestId("update-install-btn")).toBeInTheDocument();
    expect(screen.getByTestId("update-dismiss-btn")).toBeInTheDocument();
  });

  it("calls onInstall when Install button clicked", async () => {
    const onInstall = vi.fn();
    render(<UpdateBanner {...defaultProps} onInstall={onInstall} />);
    await userEvent.click(screen.getByTestId("update-install-btn"));
    expect(onInstall).toHaveBeenCalledOnce();
  });

  it("calls onDismiss when Later button clicked", async () => {
    const onDismiss = vi.fn();
    render(<UpdateBanner {...defaultProps} onDismiss={onDismiss} />);
    await userEvent.click(screen.getByTestId("update-dismiss-btn"));
    expect(onDismiss).toHaveBeenCalledOnce();
  });

  it("shows download progress when downloading", () => {
    render(
      <UpdateBanner {...defaultProps} status="downloading" progress={42} />,
    );
    expect(screen.getByText(/42%/)).toBeInTheDocument();
    expect(screen.queryByTestId("update-install-btn")).not.toBeInTheDocument();
  });
});
