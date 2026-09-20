import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AuthGate } from "./components/AuthGate";
import { Shell } from "./components/Shell";
import { PageSkeleton } from "./components/Skeleton";

// Route-level splitting: the console has five screens and a first paint
// should not wait for all of them.
const Overview = lazy(() => import("./pages/Overview"));
const Runs = lazy(() => import("./pages/Runs"));
const RunDetail = lazy(() => import("./pages/RunDetail"));
const Libraries = lazy(() => import("./pages/Libraries"));
const LibraryBrowser = lazy(() => import("./pages/LibraryBrowser"));
const Config = lazy(() => import("./pages/Config"));
const Bindings = lazy(() => import("./pages/Bindings"));

export default function App() {
  return (
    <AuthGate>
      <Routes>
      <Route element={<Shell />}>
        <Route
          index
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Overview />
            </Suspense>
          }
        />
        <Route
          path="runs"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Runs />
            </Suspense>
          }
        />
        <Route
          path="runs/:runId"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <RunDetail />
            </Suspense>
          }
        />
        <Route
          path="libraries"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Libraries />
            </Suspense>
          }
        />
        <Route
          path="libraries/:name"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <LibraryBrowser />
            </Suspense>
          }
        />
        <Route
          path="config"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Config />
            </Suspense>
          }
        />
        <Route
          path="bindings"
          element={
            <Suspense fallback={<PageSkeleton />}>
              <Bindings />
            </Suspense>
          }
        />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
      </Routes>
    </AuthGate>
  );
}
