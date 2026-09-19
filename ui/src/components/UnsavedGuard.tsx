import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

interface Unsaved {
  dirty: boolean;
  setDirty: (dirty: boolean) => void;
}

const UnsavedContext = createContext<Unsaved>({ dirty: false, setDirty: () => {} });

/** Tracks whether some page holds unsaved edits; the Shell asks before navigating away. */
export function UnsavedProvider({ children }: { children: ReactNode }) {
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (!dirty) return;
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [dirty]);

  const value = useMemo(() => ({ dirty, setDirty }), [dirty]);
  return <UnsavedContext.Provider value={value}>{children}</UnsavedContext.Provider>;
}

export const useUnsaved = () => useContext(UnsavedContext);
