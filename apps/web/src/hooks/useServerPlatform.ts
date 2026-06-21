import { useEffect, useState } from "react";
import { api } from "../api";

let cached: string | null = null;

export function useServerPlatform(): string | null {
  const [platform, setPlatform] = useState<string | null>(cached);

  useEffect(() => {
    if (cached) return;
    api
      .listBackends()
      .then((d) => {
        cached = d.platform ?? null;
        setPlatform(cached);
      })
      .catch((err: unknown) => {
        console.error("Failed to detect server platform:", err);
      });
  }, []);

  return platform;
}
