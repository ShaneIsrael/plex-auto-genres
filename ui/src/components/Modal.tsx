import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";

/** A native <dialog> with a header and close button; Escape and backdrop close it. */
export function Modal({
  open,
  onClose,
  title,
  eyebrow,
  children,
  wide = false,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  eyebrow?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`modal ${wide ? "modal--wide" : ""}`}
      onClose={onClose}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      onClick={(e) => {
        // A click on the backdrop lands on the dialog element itself.
        if (e.target === ref.current) onClose();
      }}
    >
      {open && (
        <div className="modal__frame">
          <header className="modal__head">
            <div>
              {eyebrow && <div className="label">{eyebrow}</div>}
              <h2 className="modal__title">{title}</h2>
            </div>
            <button type="button" className="iconbtn" aria-label="Close" onClick={onClose}>
              <X size={18} aria-hidden="true" />
            </button>
          </header>
          <div className="modal__body">{children}</div>
        </div>
      )}
    </dialog>
  );
}
