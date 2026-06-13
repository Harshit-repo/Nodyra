import type { BarStatus } from "./barStatus";

export function PublishPill({
  status,
  onClick,
  disabled,
}: {
  status: BarStatus;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`btn publish-pill is-${status.kind}`}
      onClick={onClick}
      disabled={disabled}
      title="Publish the current draft as a production version"
    >
      {status.dot && <span className={`publish-dot is-${status.dot}`} aria-hidden />}
      {status.pillLabel}
    </button>
  );
}
