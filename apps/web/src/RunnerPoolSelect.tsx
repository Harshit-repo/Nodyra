import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import type { RunnerPoolInfo } from "./types";

interface RunnerPoolSelectProps {
  pools: RunnerPoolInfo[];
  value: string | null;
  onChange: (v: string | null) => void;
  label?: ReactNode;
  hint?: string;
}

export function RunnerPoolSelect({ pools, value, onChange, label, hint }: RunnerPoolSelectProps) {
  const selectedRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    selectedRef.current?.scrollIntoView({ block: "nearest" });
  }, [value]);

  const localSelected = value === null;

  return (
    <>
      {label && <label className="field-label">{label}</label>}
      <div className="rps-list">
        <PoolTile
          id={null}
          name="Local (in-process)"
          sub="Runs on the API host"
          provider={null}
          onlineCount={null}
          totalCount={null}
          selected={localSelected}
          onChange={onChange}
          tileRef={localSelected ? selectedRef : undefined}
        />
        {pools.map((p) => {
          const sel = value === p.id;
          return (
            <PoolTile
              key={p.id}
              id={p.id}
              name={p.name}
              sub={`max ${p.max_concurrent_runs} concurrent`}
              provider={p.provider}
              onlineCount={p.online_count}
              totalCount={p.runner_count}
              selected={sel}
              onChange={onChange}
              tileRef={sel ? selectedRef : undefined}
            />
          );
        })}
      </div>
      {hint && <p className="field-hint muted" style={{ marginTop: 5 }}>{hint}</p>}
    </>
  );
}

function PoolTile({
  id,
  name,
  sub,
  provider,
  onlineCount,
  totalCount,
  selected,
  onChange,
  tileRef,
}: {
  id: string | null;
  name: string;
  sub: string;
  provider: string | null;
  onlineCount: number | null;
  totalCount: number | null;
  selected: boolean;
  onChange: (v: string | null) => void;
  tileRef?: RefObject<HTMLDivElement>;
}) {
  const anyOnline = onlineCount != null && onlineCount > 0;

  return (
    <div
      ref={tileRef}
      className={`rps-tile${selected ? " rps-tile--selected" : ""}`}
      onClick={() => onChange(id)}
      role="radio"
      aria-checked={selected}
      tabIndex={0}
      onKeyDown={(e) => (e.key === " " || e.key === "Enter") && onChange(id)}
    >
      <div className={`rps-radio${selected ? " rps-radio--checked" : ""}`} />
      <div className="rps-tile-body">
        <span className="rps-tile-name">{name}</span>
        <span className="rps-tile-sub">{sub}</span>
      </div>
      {provider !== null && totalCount !== null && (
        <div className="rps-tile-right">
          <span className={`rps-online${anyOnline ? " rps-online--up" : ""}`}>
            ● {onlineCount}/{totalCount}
          </span>
          <span className="rps-tag">{provider}</span>
        </div>
      )}
    </div>
  );
}
