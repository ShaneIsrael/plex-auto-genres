import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

type Tone = "ok" | "warn" | "fail" | "info";
interface Toast {
  id: number;
  tone: Tone;
  title: string;
  body?: string;
}

const ToastContext = createContext<(tone: Tone, title: string, body?: string) => void>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);

  const push = useCallback((tone: Tone, title: string, body?: string) => {
    const id = next.current++;
    setToasts((all) => [...all, { id, tone, title, body }]);
    // Auto-dismiss in the 3-5 s window; errors linger a little longer.
    window.setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), tone === "fail" ? 6000 : 4000);
  }, []);

  const dismiss = (id: number) => setToasts((all) => all.filter((t) => t.id !== id));
  const value = useMemo(() => push, [push]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* polite: announced without stealing focus */}
      <div className="toasts" aria-live="polite" aria-relevant="additions">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast--${t.tone}`} role="status">
            <div className="toast__text">
              <div className="toast__title">{t.title}</div>
              {t.body && <div className="toast__body">{t.body}</div>}
            </div>
            <button type="button" className="toast__close" onClick={() => dismiss(t.id)} aria-label="Dismiss">
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
