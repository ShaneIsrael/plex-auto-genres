import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface Request {
  title: string;
  body?: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
}

type Ask = (request: Request) => Promise<boolean>;

const ConfirmContext = createContext<Ask>(async () => false);

/** A native <dialog>: focus trapping, Escape and the backdrop come for free. */
export function ConfirmProvider({ children }: { children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  const [request, setRequest] = useState<Request | null>(null);
  const resolver = useRef<((ok: boolean) => void) | null>(null);

  const ask = useCallback<Ask>((r) => {
    setRequest(r);
    return new Promise<boolean>((resolve) => {
      resolver.current = resolve;
    });
  }, []);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (request && !dialog.open) {
      dialog.showModal();
      // showModal() focuses the first focusable control, i.e. Cancel. For a
      // destructive confirmation that is the right default (Enter must not
      // destroy anything by reflex); for a routine one, focus the action so
      // Enter confirms.
      const selector = request.danger ? "button[data-cancel]" : "button[type=submit]";
      dialog.querySelector<HTMLButtonElement>(selector)?.focus();
    }
    if (!request && dialog.open) dialog.close();
  }, [request]);

  const settle = (ok: boolean) => {
    resolver.current?.(ok);
    resolver.current = null;
    setRequest(null);
  };

  return (
    <ConfirmContext.Provider value={ask}>
      {children}
      <dialog ref={ref} className="dialog" onClose={() => settle(false)} onCancel={(e) => { e.preventDefault(); settle(false); }}>
        {request && (
          <form method="dialog" className="dialog__form" onSubmit={(e) => { e.preventDefault(); settle(true); }}>
            <h2 className="dialog__title">{request.title}</h2>
            {request.body && <div className="dialog__body muted">{request.body}</div>}
            <div className="dialog__actions">
              <button type="button" className="button button--ghost" data-cancel onClick={() => settle(false)}>
                Cancel
              </button>
              <button type="submit" className={`button ${request.danger ? "button--danger" : ""}`}>
                {request.confirmLabel ?? "Confirm"}
              </button>
            </div>
          </form>
        )}
      </dialog>
    </ConfirmContext.Provider>
  );
}

export const useConfirm = () => useContext(ConfirmContext);
