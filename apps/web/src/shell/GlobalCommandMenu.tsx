import { Command, MagnifyingGlass, X } from "@phosphor-icons/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useLocation, useNavigate } from "react-router-dom";

import type { UserInfo } from "../types";
import { APP_ROUTES, canAccessNavigationRoute } from "./navigation";
import { requestShellOverlayOwnership } from "./overlay";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [href], [tabindex]:not([tabindex='-1'])";

export function GlobalCommandMenu({
  user,
  workspaceRole,
  localMode = false,
  multiTenancyEnabled = false,
}: {
  user: Pick<UserInfo, "role"> | null;
  workspaceRole?: string | null;
  localMode?: boolean;
  multiTenancyEnabled?: boolean;
}) {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const routes = useMemo(
    () =>
      APP_ROUTES.filter((route) =>
        canAccessNavigationRoute(
          route,
          user,
          workspaceRole,
          localMode,
          multiTenancyEnabled,
        ),
      ),
    [localMode, multiTenancyEnabled, user, workspaceRole],
  );
  const results = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    if (!normalized) return routes;
    return routes.filter((route) =>
      `${route.label} ${route.group}`.toLowerCase().includes(normalized),
    );
  }, [query, routes]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActiveIndex(0);
  }, []);

  useEffect(() => {
    function onShortcut(event: KeyboardEvent): void {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        if (event.defaultPrevented) return;
        event.preventDefault();
        setOpen((value) => {
          const next = !value;
          if (next) requestShellOverlayOwnership();
          if (!next) window.requestAnimationFrame(() => triggerRef.current?.focus());
          return next;
        });
      }
    }
    document.addEventListener("keydown", onShortcut);
    return () => document.removeEventListener("keydown", onShortcut);
  }, []);

  useEffect(() => {
    close();
  }, [close, pathname]);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const animationFrame = window.requestAnimationFrame(() => inputRef.current?.focus());
    return () => {
      window.cancelAnimationFrame(animationFrame);
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  useEffect(() => {
    if (!open) return;

    function onKeyDown(event: KeyboardEvent): void {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
        triggerRef.current?.focus();
        return;
      }
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setActiveIndex((value) => Math.min(value + 1, Math.max(0, results.length - 1)));
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setActiveIndex((value) => Math.max(0, value - 1));
        return;
      }
      if (event.key === "Home") {
        event.preventDefault();
        setActiveIndex(0);
        return;
      }
      if (event.key === "End") {
        event.preventDefault();
        setActiveIndex(Math.max(0, results.length - 1));
        return;
      }
      if (event.key === "Enter" && results[activeIndex]) {
        event.preventDefault();
        const route = results[activeIndex];
        const currentRoute = route.matches(pathname);
        navigate(route.href);
        close();
        if (currentRoute) triggerRef.current?.focus();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(
        dialogRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [],
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
    };
  }, [activeIndex, close, navigate, open, pathname, results]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  useEffect(() => {
    const active = results[activeIndex];
    if (!open || !active) return;
    document
      .getElementById(`nodyra-command-${active.id}`)
      ?.scrollIntoView?.({ block: "nearest" });
  }, [activeIndex, open, results]);

  return (
    <>
      <button
        ref={triggerRef}
        className="nodyra-shell-command-trigger"
        type="button"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => {
          requestShellOverlayOwnership();
          setOpen(true);
        }}
      >
        <MagnifyingGlass size={17} aria-hidden="true" />
        <span>Go to pages and commands</span>
        <kbd><Command size={11} aria-hidden="true" />K</kbd>
      </button>

      {open && createPortal(
        <div
          className="nodyra-shell-command-layer"
          onClick={(event) => {
            if (event.target === event.currentTarget) {
              close();
              triggerRef.current?.focus();
            }
          }}
        >
          <div
            ref={dialogRef}
            className="nodyra-shell-command-dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="nodyra-command-title"
          >
            <div className="nodyra-shell-command-head">
              <MagnifyingGlass size={19} aria-hidden="true" />
              <input
                ref={inputRef}
                role="combobox"
                aria-label="Search pages and commands"
                aria-autocomplete="list"
                aria-expanded="true"
                value={query}
                aria-controls="nodyra-command-results"
                aria-activedescendant={results[activeIndex] ? `nodyra-command-${results[activeIndex].id}` : undefined}
                placeholder="Search pages and commands…"
                onChange={(event) => setQuery(event.target.value)}
              />
              <button
                type="button"
                aria-label="Close command menu"
                onClick={() => {
                  close();
                  triggerRef.current?.focus();
                }}
              >
                <X size={18} aria-hidden="true" />
              </button>
            </div>
            <div className="nodyra-shell-command-label" id="nodyra-command-title">Navigate</div>
            <div id="nodyra-command-results" className="nodyra-shell-command-results" role="listbox" aria-label="Pages">
              {results.length === 0 ? (
                <div className="nodyra-shell-command-empty">No matching pages or commands.</div>
              ) : results.map((route, index) => {
                const Icon = route.icon;
                return (
                  <button
                    id={`nodyra-command-${route.id}`}
                    key={route.id}
                    type="button"
                    tabIndex={-1}
                    role="option"
                    aria-selected={index === activeIndex}
                    className={index === activeIndex ? "is-active" : ""}
                    onMouseEnter={() => setActiveIndex(index)}
                    onClick={() => {
                      const currentRoute = route.matches(pathname);
                      navigate(route.href);
                      close();
                      if (currentRoute) triggerRef.current?.focus();
                    }}
                  >
                    <Icon size={19} aria-hidden="true" />
                    <span><strong>{route.label}</strong><small>{route.scope}</small></span>
                    <kbd>↵</kbd>
                  </button>
                );
              })}
            </div>
            <div className="nodyra-shell-command-footer"><span>↑↓ Navigate</span><span>↵ Open</span><span>Esc Close</span></div>
          </div>
        </div>,
        document.body,
      )}
    </>
  );
}
