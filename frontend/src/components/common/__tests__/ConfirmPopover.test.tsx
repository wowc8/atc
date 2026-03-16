import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import ConfirmPopover from "../ConfirmPopover";

describe("ConfirmPopover", () => {
  it("renders children", () => {
    render(
      <ConfirmPopover message="Are you sure?" onConfirm={vi.fn()}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    expect(screen.getByText("Delete")).toBeInTheDocument();
  });

  it("does not show popover initially", () => {
    render(
      <ConfirmPopover message="Are you sure?" onConfirm={vi.fn()}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    expect(screen.queryByTestId("confirm-popover")).not.toBeInTheDocument();
  });

  it("shows popover when trigger is clicked", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmPopover message="Delete this item?" onConfirm={vi.fn()}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    expect(screen.getByTestId("confirm-popover")).toBeInTheDocument();
    expect(screen.getByText("Delete this item?")).toBeInTheDocument();
  });

  it("calls onConfirm and closes when confirmed", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmPopover message="Are you sure?" onConfirm={onConfirm}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    await user.click(screen.getByTestId("confirm-popover-confirm"));
    expect(onConfirm).toHaveBeenCalledOnce();
    expect(screen.queryByTestId("confirm-popover")).not.toBeInTheDocument();
  });

  it("closes without calling onConfirm when cancelled", async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    render(
      <ConfirmPopover message="Are you sure?" onConfirm={onConfirm}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    await user.click(screen.getByText("Cancel"));
    expect(onConfirm).not.toHaveBeenCalled();
    expect(screen.queryByTestId("confirm-popover")).not.toBeInTheDocument();
  });

  it("closes on Escape key", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmPopover message="Are you sure?" onConfirm={vi.fn()}>
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    expect(screen.getByTestId("confirm-popover")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByTestId("confirm-popover")).not.toBeInTheDocument();
  });

  it("closes on click outside", async () => {
    const user = userEvent.setup();
    const { container } = render(
      <div>
        <ConfirmPopover message="Are you sure?" onConfirm={vi.fn()}>
          <button>Delete</button>
        </ConfirmPopover>
        <div data-testid="outside">Outside</div>
      </div>,
    );
    await user.click(screen.getByText("Delete"));
    expect(screen.getByTestId("confirm-popover")).toBeInTheDocument();
    await user.click(container.ownerDocument.body);
    expect(screen.queryByTestId("confirm-popover")).not.toBeInTheDocument();
  });

  it("uses custom button labels", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmPopover
        message="Sure?"
        confirmLabel="Yes, delete"
        cancelLabel="Nah"
        onConfirm={vi.fn()}
      >
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    expect(screen.getByText("Yes, delete")).toBeInTheDocument();
    expect(screen.getByText("Nah")).toBeInTheDocument();
  });

  it("applies danger variant", async () => {
    const user = userEvent.setup();
    render(
      <ConfirmPopover
        message="Sure?"
        variant="danger"
        onConfirm={vi.fn()}
      >
        <button>Delete</button>
      </ConfirmPopover>,
    );
    await user.click(screen.getByText("Delete"));
    const confirmBtn = screen.getByTestId("confirm-popover-confirm");
    expect(confirmBtn.className).toContain("btn-danger");
  });
});
