import { useState, useRef, useEffect, type ReactNode } from "react";
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
  const popoverRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    function handleClickOutside(e: MouseEvent) {
      if (
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
    <div className="confirm-popover-wrapper" ref={popoverRef}>
      <div onClick={() => setOpen(true)}>{children}</div>
      {open && (
        <div className="confirm-popover" data-testid="confirm-popover">
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
