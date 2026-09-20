import { lazy, Suspense, type ComponentType } from "react";
import { Navigate, Outlet, Route } from "react-router-dom";
import { AuthGate } from "./components/AuthGate";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { Shell } from "./components/Shell";
import { PageSkeleton } from "./components/Skeleton";
import { ToastProvider } from "./components/Toast";
import { UnsavedProvider } from "./components/UnsavedGuard";

// Route-level splitting: the console has a handful of screens and a first
// paint should not wait for all of them. Each page gets its own Suspense
// boundary so switching screens shows the skeleton, not a blank shell.
const PAGES: { path?: string; Page: ComponentType }[] = [
  { Page: lazy(() => import("./pages/Overview")) },
  { path: "runs", Page: lazy(() => import("./pages/Runs")) },
  { path: "runs/:runId", Page: lazy(() => import("./pages/RunDetail")) },
  { path: "libraries", Page: lazy(() => import("./pages/Libraries")) },
  { path: "libraries/:name", Page: lazy(() => import("./pages/LibraryBrowser")) },
  { path: "config", Page: lazy(() => import("./pages/Config")) },
  { path: "bindings", Page: lazy(() => import("./pages/Bindings")) },
];

/** Providers that need the router (the unsaved-edits blocker) live here. */
function Root() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <UnsavedProvider>
          <AuthGate>
            <Outlet />
          </AuthGate>
        </UnsavedProvider>
      </ConfirmProvider>
    </ToastProvider>
  );
}

export const routes = (
  <Route element={<Root />}>
    <Route element={<Shell />}>
      {PAGES.map(({ path, Page }) => (
        <Route
          key={path ?? "index"}
          index={path === undefined}
          path={path}
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Page />
            </Suspense>
          }
        />
      ))}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Route>
  </Route>
);
