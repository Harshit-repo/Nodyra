import type { CSSProperties } from "react";

export type SkeletonVariant = "text" | "title" | "card" | "circle" | "rect" | "row";

interface SkeletonProps {
  variant?: SkeletonVariant;
  width?: string | number;
  height?: string | number;
  className?: string;
}

const BASE_CLASSES: Record<SkeletonVariant, string> = {
  text: "skeleton skeleton-text",
  title: "skeleton skeleton-title",
  card: "skeleton skeleton-card",
  circle: "skeleton skeleton-circle",
  rect: "skeleton skeleton-rect",
  row: "skeleton skeleton-row",
};

/**
 * Lightweight skeleton placeholder for loading states.
 *
 * Use `variant` for common shapes or pass explicit `width`/`height` for custom
 * sizes. All variants respect `prefers-reduced-motion`.
 *
 * Common patterns:
 *   <Skeleton variant="text" />           — single line of text
 *   <Skeleton variant="title" />          — heading placeholder
 *   <Skeleton variant="card" />           — workflow/credential card shape
 *   <Skeleton variant="circle" width={32} /> — avatar
 *   <Skeleton variant="rect" width="100%" height={120} /> — arbitrary block
 *   <Skeleton variant="row" />            — full-width table row
 */
export function Skeleton({
  variant = "text",
  width,
  height,
  className = "",
}: SkeletonProps) {
  const style: CSSProperties = {};
  if (width !== undefined) style.width = typeof width === "number" ? `${width}px` : width;
  if (height !== undefined) style.height = typeof height === "number" ? `${height}px` : height;

  return (
    <div
      className={`${BASE_CLASSES[variant]}${className ? ` ${className}` : ""}`}
      style={style}
      aria-hidden="true"
    />
  );
}

/**
 * Convenience: renders `count` skeleton cards in a grid, matching the workflow
 * list pattern. Use as a loading fallback on list pages.
 */
export function SkeletonCardGrid({
  count = 6,
  className = "",
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div className={`wf-grid${className ? ` ${className}` : ""}`} aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} variant="card" />
      ))}
    </div>
  );
}

/**
 * Convenience: renders `count` skeleton table rows.
 */
export function SkeletonRows({
  count = 5,
  className = "",
}: {
  count?: number;
  className?: string;
}) {
  return (
    <div aria-label="Loading">
      {Array.from({ length: count }).map((_, i) => (
        <Skeleton key={i} variant="row" className={className} />
      ))}
    </div>
  );
}
