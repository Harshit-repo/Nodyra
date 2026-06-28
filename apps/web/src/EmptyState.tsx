import type { ReactNode } from "react";

export interface EmptyStateProps {
  /** Phosphor or custom icon element */
  icon?: ReactNode;
  /** Main heading */
  title: string;
  /** Supporting description */
  description?: string;
  /** Primary action button */
  action?: ReactNode;
  /** Secondary content (links, template strip, etc.) */
  children?: ReactNode;
}

/**
 * Consistent empty-state pattern used across all pages.
 *
 * Shows an icon/illustration, heading, description, a primary call-to-action,
 * and optional secondary content. Use everywhere a list, table, or grid is
 * empty — never leave a user staring at a blank page.
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
  children,
}: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon}
      <h2>{title}</h2>
      {description && <p className="muted">{description}</p>}
      {action && <div className="empty-state-action">{action}</div>}
      {children}
    </div>
  );
}
