import { useQueryClient } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { UNAUTHORIZED_EVENT, useAuthStatus } from "../api/client";
import { ErrorBlock } from "./Empty";
import { Login } from "./Login";

/**
 * Shows the console only to a signed-in client. Any 401 from the API flips
 * the gate back to the login screen, so an expired session is handled once,
 * here, instead of in every page.
 */
export function AuthGate({ children }: { children: ReactNode }) {
  const status = useAuthStatus();
  const qc = useQueryClient();

  useEffect(() => {
    const onUnauthorized = () => void qc.invalidateQueries({ queryKey: ["auth"] });
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized);
  }, [qc]);

  if (status.isPending) {
    return <div className="gate gate--busy" aria-busy="true" aria-label="Checking session" />;
  }
  if (status.isError) {
    return (
      <div className="gate">
        <div className="login__card">
          <ErrorBlock error={status.error} onRetry={() => status.refetch()} />
        </div>
      </div>
    );
  }
  if (status.data.enabled && !status.data.authenticated) {
    return <Login />;
  }
  return <>{children}</>;
}
