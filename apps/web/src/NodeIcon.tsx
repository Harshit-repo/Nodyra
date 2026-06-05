import {
  Archive,
  ArrowLineDown,
  ArrowSquareIn,
  ArrowsClockwise,
  BracketsCurly,
  Calendar,
  ChartBar,
  ChartLineUp,
  ChatCircle,
  CircleDashed,
  Clock,
  Code,
  Columns,
  Copy,
  Cpu,
  CreditCard,
  Database,
  Envelope,
  Eye,
  File,
  FloppyDisk,
  Funnel,
  Gauge,
  GitBranch,
  GitMerge,
  GithubLogo,
  Globe,
  HardDrive,
  Hash,
  Key,
  Layout,
  List,
  Package,
  Pause,
  PencilSimple,
  Play,
  Robot,
  Ruler,
  Shuffle,
  SortAscending,
  Sparkle,
  Stack,
  Table,
  Tag,
  Terminal,
  TextT,
  Warning,
  WebhooksLogo,
  type Icon,
} from "@phosphor-icons/react";
import { useState } from "react";

const ICON_MAP: Record<string, Icon> = {
  play: Play,
  clock: Clock,
  webhook: WebhooksLogo,
  alert: Warning,
  branch: GitBranch,
  switch: Shuffle,
  filter: Funnel,
  merge: GitMerge,
  repeat: ArrowsClockwise,
  pencil: PencilSimple,
  sort: SortAscending,
  limit: ArrowLineDown,
  aggregate: Stack,
  dedupe: Copy,
  tag: Tag,
  ruler: Ruler,
  code: Code,
  globe: Globe,
  key: Key,
  calendar: Calendar,
  braces: BracketsCurly,
  import: ArrowSquareIn,
  regex: TextT,
  hash: Hash,
  page: File,
  table: Table,
  eye: Eye,
  list: List,
  columns: Columns,
  terminal: Terminal,
  "bar-chart": ChartBar,
  layout: Layout,
  ai: Robot,
  dot: CircleDashed,
  pause: Pause,
  message: ChatCircle,
  mail: Envelope,
  sheet: Table,
  github: GithubLogo,
  database: Database,
  storage: HardDrive,
  card: CreditCard,
  cpu: Cpu,
  sparkles: Sparkle,
  gauge: Gauge,
  save: FloppyDisk,
  archive: Archive,
  package: Package,
  activity: ChartLineUp, // Activity not in this version; using ChartLineUp
};

const BRAND_FALLBACK_ICON_MAP: Record<string, Icon> = {
  airtable: Table,
  github: GithubLogo,
  googlesheets: Table,
  microsoftoutlook: Envelope,
  notion: File,
  slack: ChatCircle,
  stripe: CreditCard,
};

function BrandNodeIcon({
  slug,
  size,
  className,
}: {
  slug: string;
  size: number;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const IconComponent = BRAND_FALLBACK_ICON_MAP[slug.toLowerCase()] ?? CircleDashed;

  if (failed) {
    return (
      <IconComponent
        size={size}
        weight="regular"
        aria-hidden
        className={className}
      />
    );
  }

  return (
    <img
      src={`https://cdn.simpleicons.org/${encodeURIComponent(slug)}`}
      width={size}
      height={size}
      alt=""
      className={`node-brand-icon${className ? ` ${className}` : ""}`}
      onError={() => setFailed(true)}
    />
  );
}

export function isBrandIconName(
  name: string | null | undefined,
): name is `brand:${string}` {
  return typeof name === "string" && name.startsWith("brand:");
}

export function NodeIcon({
  name,
  size = 20,
  className,
}: {
  name: string | null | undefined;
  size?: number;
  className?: string;
}) {
  // Real brand logos via SimpleIcons CDN. Convention: `brand:<slug>` where
  // <slug> matches https://simpleicons.org (e.g. brand:stripe, brand:openai).
  // Falls back to a local generic icon if the network request fails.
  if (isBrandIconName(name)) {
    const slug = name.slice("brand:".length);
    return <BrandNodeIcon slug={slug} size={size} className={className} />;
  }

  const IconComponent = (name ? ICON_MAP[name] : undefined) ?? CircleDashed;
  return (
    <IconComponent
      size={size}
      weight="regular"
      aria-hidden
      className={className}
    />
  );
}
