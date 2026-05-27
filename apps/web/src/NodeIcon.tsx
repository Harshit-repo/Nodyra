import type { ReactNode } from "react";

const ICONS: Record<string, ReactNode> = {
  play: <path d="M8 5.5 19 12 8 18.5z" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </>
  ),
  webhook: (
    <>
      <circle cx="12" cy="6" r="2.6" />
      <circle cx="6" cy="17" r="2.6" />
      <circle cx="18" cy="17" r="2.6" />
      <path d="M10.4 7.6 7.3 14.6M13.6 7.6l3.1 7M8.7 17h6.6" />
    </>
  ),
  branch: (
    <>
      <circle cx="6.5" cy="6" r="2.4" />
      <circle cx="6.5" cy="18" r="2.4" />
      <circle cx="17.5" cy="9" r="2.4" />
      <path d="M6.5 8.4v7.2M6.5 12h6.6a3 3 0 0 0 3-3" />
    </>
  ),
  switch: <path d="M4 8h10l3.5-3M4 8l3.5 3M4 16h10l3.5 3M4 16l3.5-3" />,
  filter: <path d="M4 5.5h16l-6.2 7.6V19l-3.6-2v-4.4z" />,
  merge: (
    <>
      <path d="M5 5v4.5a4.5 4.5 0 0 0 4.5 4.5H19" />
      <path d="M5 19v-4.5a4.5 4.5 0 0 1 4.5-4.5H19" />
      <path d="m16 7 3 3-3 3" />
    </>
  ),
  pencil: (
    <>
      <path d="M4 20h4L19.5 8.5 15.5 4.5 4 16z" />
      <path d="m13.5 6.5 4 4" />
    </>
  ),
  sort: <path d="M7 4.5v15M7 4.5 4 8M7 4.5 10 8M13 7.5h8M13 12.5h5.5M13 17.5h3" />,
  limit: <path d="M5 5v14h4M5 5h4M19 5v14h-4M19 5h-4M9.5 12h5" />,
  aggregate: <path d="M3.5 6.5h17M7 12h10M10.5 17.5h3" />,
  dedupe: (
    <>
      <rect x="4" y="4" width="11" height="11" rx="2.5" />
      <rect x="9" y="9" width="11" height="11" rx="2.5" />
    </>
  ),
  tag: (
    <>
      <path d="M4 11.5 11.5 4H20v8.5L12.5 20z" />
      <circle cx="15.5" cy="8.5" r="1.6" />
    </>
  ),
  code: <path d="m9 7-5 5 5 5M15 7l5 5-5 5" />,
  globe: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M3.5 12h17M12 3.5c2.6 2.6 2.6 14.4 0 17M12 3.5c-2.6 2.6-2.6 14.4 0 17" />
    </>
  ),
  calendar: (
    <>
      <rect x="4" y="5.5" width="16" height="14.5" rx="2.5" />
      <path d="M4 10h16M9 3v4.5M15 3v4.5" />
    </>
  ),
  braces: (
    <>
      <path d="M9.5 4C7.6 4 7 5 7 6.8v2.4C7 10.6 6 11.6 4.5 11.8 6 12 7 13 7 14.4v2.8C7 19 7.6 20 9.5 20" />
      <path d="M14.5 4c1.9 0 2.5 1 2.5 2.8v2.4c0 1.4 1 2.4 2.5 2.6-1.5.2-2.5 1.2-2.5 2.6v2.8c0 1.8-.6 2.8-2.5 2.8" />
    </>
  ),
  import: <path d="M12 3.5v11M12 14.5 8 10.5M12 14.5l4-4M5 19.5h14" />,
  message: (
    <>
      <path d="M4.5 5.5h15v10h-8L7 19v-3.5H4.5z" />
      <path d="M8 9h8M8 12h5" />
    </>
  ),
  mail: (
    <>
      <rect x="3.5" y="6" width="17" height="12" rx="2.5" />
      <path d="m4.5 8 7.5 5 7.5-5" />
    </>
  ),
  sheet: (
    <>
      <rect x="5" y="3.5" width="14" height="17" rx="2" />
      <path d="M8 8h8M8 12h8M8 16h5M11 8v8" />
    </>
  ),
  page: (
    <>
      <path d="M6 3.5h8l4 4V20H6z" />
      <path d="M14 3.5V8h4M9 12h6M9 16h5" />
    </>
  ),
  github: (
    <>
      <path d="M9 19.5c-4.5 1.3-4.5-2.2-6.3-2.7" />
      <path d="M15 21v-3.7c0-1 .2-1.7-.5-2.3 2.5-.3 5-1.2 5-5.4 0-1.2-.4-2.2-1.1-3 .1-.3.5-1.5-.1-3 0 0-.9-.3-3 1.1a10 10 0 0 0-5.4 0c-2.1-1.4-3-1.1-3-1.1-.6 1.5-.2 2.7-.1 3a4.3 4.3 0 0 0-1.1 3c0 4.2 2.5 5.1 5 5.4-.4.3-.6.9-.6 1.7V21" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v6c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
      <path d="M5 12v6c0 1.7 3.1 3 7 3s7-1.3 7-3v-6" />
    </>
  ),
  storage: (
    <>
      <path d="M4.5 8 12 4l7.5 4-7.5 4z" />
      <path d="M4.5 12 12 16l7.5-4M4.5 16 12 20l7.5-4" />
    </>
  ),
  ai: (
    <>
      <path d="M12 3.5v3M12 17.5v3M3.5 12h3M17.5 12h3" />
      <path d="M8.5 8.5h7v7h-7z" />
      <path d="M10.5 12h3M12 10.5v3" />
    </>
  ),
  card: (
    <>
      <rect x="3.5" y="6" width="17" height="12" rx="2.5" />
      <path d="M3.5 10h17M7 14.5h4" />
    </>
  ),
  table: (
    <>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M4 10h16M4 15h16M9 5v14M15 5v14" />
    </>
  ),
  dot: <circle cx="12" cy="12" r="7" />,
  pause: (
    <>
      <rect x="6.5" y="5" width="4" height="14" rx="1.4" />
      <rect x="13.5" y="5" width="4" height="14" rx="1.4" />
    </>
  ),
  hash: <path d="M9 4 7 20M17 4 15 20M4.5 10h16M3.5 14h16" />,
  regex: (
    <>
      <path d="M5 18 19 6" />
      <circle cx="9" cy="15" r="1.2" />
      <circle cx="15" cy="9" r="1.2" />
    </>
  ),
  ruler: (
    <>
      <rect x="3.5" y="10" width="17" height="6" rx="1.5" />
      <path d="M7 10v3M11 10v4M15 10v3M19 10v4" />
    </>
  ),
};

export function NodeIcon({
  name,
  size = 24,
}: {
  name: string | null | undefined;
  size?: number;
}) {
  // Real brand logos via SimpleIcons CDN. Convention: `brand:<slug>` where
  // <slug> matches https://simpleicons.org (e.g. brand:stripe, brand:openai).
  // Falls back to the dot icon if the network request fails.
  if (typeof name === "string" && name.startsWith("brand:")) {
    const slug = name.slice("brand:".length);
    return (
      <img
        src={`https://cdn.simpleicons.org/${encodeURIComponent(slug)}`}
        width={size}
        height={size}
        alt=""
        className="node-brand-icon"
        loading="lazy"
        onError={(e) => {
          // Hide the broken image so the node card doesn't show a torn-image glyph.
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
    );
  }
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {ICONS[name ?? ""] ?? ICONS.dot}
    </svg>
  );
}
