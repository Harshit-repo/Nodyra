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
  type Icon,
} from "@phosphor-icons/react";

const ICON_MAP: Record<string, Icon> = {
  play: Play,
  clock: Clock,
  webhook: ArrowsClockwise,
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
  // Falls back to the dot icon if the network request fails.
  if (typeof name === "string" && name.startsWith("brand:")) {
    const slug = name.slice("brand:".length);
    return (
      <img
        src={`https://cdn.simpleicons.org/${encodeURIComponent(slug)}`}
        width={size}
        height={size}
        alt=""
        className={`node-brand-icon${className ? ` ${className}` : ""}`}
        loading="lazy"
        onError={(e) => {
          // Hide the broken image so the node card doesn't show a torn-image glyph.
          (e.currentTarget as HTMLImageElement).style.display = "none";
        }}
      />
    );
  }

  const IconComponent = (name ? ICON_MAP[name] : undefined) ?? CircleDashed;
  return (
    <IconComponent
      size={size}
      weight="duotone"
      aria-hidden
      className={className}
    />
  );
}
