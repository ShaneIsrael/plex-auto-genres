import { createContext, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useBlocker } from "react-router-dom";
import { useConfirm } from "./ConfirmDialog";

interface Unsaved {
  dirty: boolean;
  setDirty: (dirty: boolean) => void;
}

const UnsavedContext = createContext<Unsaved>({ dirty: false, setDirty: () => {} });

/**
 * Tracks whether some page holds unsaved edits. Every in-app navigation —
 * rail, status strip, the brand link, the Back button — goes through the
 * router's blocker and asks first; `beforeunload` covers reloads and
 * closed tabs. Must live inside the router.
 */
export function UnsavedProvider({ children }: { children: ReactNode }) {
  const [dirty, setDirty] = useState(false);
  const confirm = useConfirm();
  const confirmRef = useRef(confirm);
  confirmRef.current = confirm;

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => dirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    let stale = false;
    void confirmRef
      .current({ title: "Leave without saving?", body: "Your edits to the config will be lost.", confirmLabel: "Leave", danger: true })
      .then((ok) => {
        if (stale) return;
        if (ok) blocker.proceed();
        else blocker.reset();
      });
    return () => {
      stale = true;
    };
  }, [blocker]);

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
