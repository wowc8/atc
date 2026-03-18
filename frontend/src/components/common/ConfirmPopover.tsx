import { useState, useRef, useEffect, useCallback, type ReactNode } from "react";
import "./ConfirmPopover.css";

interface ConfirmPopoverProps {
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  onConfirm: () => void;
  children: ReactNode;
  variant?: "danger" | "default";
}

export default function ConfirmPopover({
  message,
  confirmLabel = "Confirm",
  cancelLabel = "Cancel",
  onConfirm,
  children,
  variant = "default",
}: ConfirmPopoverProps) {
  const [open, setOpen] = useState(false);
  const wrapperRef = useRef<HTMLDivElement>(null);
  const popoverRef = useRef<HTMLDivElement>(null);

  const updatePosition = useCallback(() => {
    const trigger = wrapperRef.current;
    const popover = popoverRef.current;
    if (!trigger || !popover) return;

    const rect = trigger.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();

    // Try to position above the trigger, centered
    let top = rect.top - popRect.height - 8;
    let left = rect.left + rect.width / 2 - popRect.width / 2;

    // If clipped at top, position below instead
    if (top < 8) {
      top = rect.bottom + 8;
    }

    // Keep within horizontal bounds
    if (left < 8) left = 8;
    if (left + popRect.width > window.innerWidth - 8) {
      left = window.innerWidth - popRect.width - 8;
    }

    popover.style.top = `${top}px`;
    popover.style.left = `${left}px`;
  }, []);

  useEffect(() => {
    if (!open) return;
    requestAnimationFrame(updatePosition);
  }, [open, updatePosition]);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(e.target as Node) &&
        popoverRef.current &&
        !popoverRef.current.contains(e.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleEscape(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", handleEscape);
    return () => document.removeEventListener("keydown", handleEscape);
  }, [open]);

  return (
    <div className="confirm-popover-wrapper" ref={wrapperRef}>
      <div onClick={() => setOpen(true)}>{children}</div>
      {open && (
        <div className="confirm-popover" ref={popoverRef} data-testid="confirm-popover">
          <p className="confirm-popover__message">{message}</p>
          <div className="confirm-popover__actions">
            <button
              className="btn btn-sm"
              onClick={() => setOpen(false)}
            >
              {cancelLabel}
            </button>
            <button
              className={`btn btn-sm ${variant === "danger" ? "btn-danger" : "btn-primary"}`}
              onClick={() => {
                onConfirm();
                setOpen(false);
              }}
              data-testid="confirm-popover-confirm"
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
