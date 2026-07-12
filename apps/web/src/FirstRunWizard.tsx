import {
  ArrowLeft,
  ArrowRight,
  CheckCircle,
  Key,
  RocketLaunch,
  ShieldCheck,
  X,
} from "@phosphor-icons/react";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { safeGetItem, safeSetItem } from "./safeStorage";
import type { AuthState } from "./types";
import { useModalA11y } from "./useModalA11y";

const STORAGE_PREFIX = "nodyra-first-run-wizard-v1";

type FirstRunStep = {
  id: string;
  title: string;
  body: string;
  icon: typeof ShieldCheck;
  cta?: {
    label: string;
    to: string;
  };
};

export function firstRunStorageKey(auth: AuthState): string {
  const scope = auth.user?.id ?? (auth.auth_required ? "signed-out" : "local");
  return `${STORAGE_PREFIX}:${scope}`;
}

function isAdminRole(role: string | undefined): boolean {
  return role === "admin" || role === "owner";
}

function accessStep(auth: AuthState): FirstRunStep {
  if (!auth.auth_required) {
    return {
      id: "admin",
      title: "Local admin ready",
      body: "This installation is running without login. Secure it before exposing production traffic.",
      icon: ShieldCheck,
    };
  }
  const user = auth.user;
  const display = user?.name || user?.email || "Your account";
  if (isAdminRole(user?.role)) {
    return {
      id: "admin",
      title: "Admin confirmed",
      body: `${display} can manage instance setup and workspace access.`,
      icon: ShieldCheck,
    };
  }
  return {
    id: "admin",
    title: "Account confirmed",
    body: `${display} is signed in as ${user?.role ?? "a user"}. Ask an instance admin for setup changes.`,
    icon: ShieldCheck,
  };
}

function stepsFor(auth: AuthState): FirstRunStep[] {
  return [
    accessStep(auth),
    {
      id: "credential",
      title: "Connect a credential",
      body: "Add the first service credential when the workflow needs external API access.",
      icon: Key,
      cta: { label: "Open credentials", to: "/credentials" },
    },
    {
      id: "template",
      title: "Run a template",
      body: "Start from the template gallery and run the workflow from the editor.",
      icon: RocketLaunch,
      cta: { label: "Open templates", to: "/" },
    },
  ];
}

export function FirstRunWizard({ auth }: { auth: AuthState }) {
  const key = useMemo(() => firstRunStorageKey(auth), [auth.auth_required, auth.user?.id]);
  const steps = useMemo(() => stepsFor(auth), [auth]);
  const [open, setOpen] = useState(() => safeGetItem(key) !== "done");
  const [stepIndex, setStepIndex] = useState(0);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setStepIndex(0);
    setOpen(safeGetItem(key) !== "done");
  }, [key]);

  function complete(): void {
    safeSetItem(key, "done");
    setOpen(false);
  }

  useModalA11y(dialogRef, complete, { enabled: open });

  if (!open) return null;

  const current = steps[stepIndex] ?? steps[0];
  const Icon = current.icon;
  const isFirst = stepIndex === 0;
  const isLast = stepIndex === steps.length - 1;

  return (
    <div className="modal-overlay first-run-overlay" onClick={complete}>
      <div
        ref={dialogRef}
        className="modal first-run-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="first-run-title"
        tabIndex={-1}
        onClick={(event) => event.stopPropagation()}
      >
        <header className="first-run-head">
          <div>
            <span className="mono-tag">First run</span>
            <h2 id="first-run-title">Workspace setup</h2>
          </div>
          <button className="btn btn-sm btn-ghost" type="button" onClick={complete} aria-label="Dismiss first-run setup">
            <X size={14} weight="bold" aria-hidden="true" />
          </button>
        </header>

        <ol className="first-run-progress" aria-label="Setup steps">
          {steps.map((step, index) => (
            <li key={step.id} className={index <= stepIndex ? "is-active" : ""}>
              <span>{index + 1}</span>
              {step.title}
            </li>
          ))}
        </ol>

        <div className="first-run-step">
          <span className="first-run-step-icon" aria-hidden="true">
            <Icon size={22} />
          </span>
          <div>
            <h3>{current.title}</h3>
            <p>{current.body}</p>
            {current.cta && (
              <Link className="btn btn-sm" to={current.cta.to} onClick={complete}>
                {current.cta.label}
                <ArrowRight size={14} weight="bold" aria-hidden="true" />
              </Link>
            )}
          </div>
        </div>

        <div className="modal-actions first-run-actions">
          <button className="btn btn-ghost" type="button" onClick={complete}>
            Dismiss
          </button>
          <div>
            <button
              className="btn"
              type="button"
              onClick={() => setStepIndex((value) => Math.max(0, value - 1))}
              disabled={isFirst}
            >
              <ArrowLeft size={14} weight="bold" aria-hidden="true" />
              Back
            </button>
            {isLast ? (
              <button className="btn btn-primary" type="button" onClick={complete}>
                <CheckCircle size={15} weight="bold" aria-hidden="true" />
                Done
              </button>
            ) : (
              <button
                className="btn btn-primary"
                type="button"
                onClick={() => setStepIndex((value) => Math.min(steps.length - 1, value + 1))}
              >
                Continue
                <ArrowRight size={14} weight="bold" aria-hidden="true" />
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
