import { QueryClient } from "@tanstack/react-query";
import { ApiError } from "../api";

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // Only retry on network/server errors (5xx). Never retry 4xx responses
      // (auth failures, not-found, forbidden) — those won't resolve on retry
      // and only delay surfacing the error to the user (F-11).
      retry: (count, error) =>
        count < 1 && (!(error instanceof ApiError) || error.status >= 500),
      refetchOnWindowFocus: false,
    },
  },
});
