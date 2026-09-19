import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ConfirmProvider } from "./components/ConfirmDialog";
import { ToastProvider } from "./components/Toast";
import "./styles/base.css";
import "./styles/components.css";
import "./styles/pages.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: (count, error) => {
        // A 5xx from our own API is worth one retry; a 404/503 is not.
        const status = (error as { status?: number }).status ?? 500;
        return status >= 500 && status !== 503 && count < 1;
      },
      refetchOnWindowFocus: true,
      staleTime: 10_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <ToastProvider>
          <ConfirmProvider>
            <App />
          </ConfirmProvider>
        </ToastProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
