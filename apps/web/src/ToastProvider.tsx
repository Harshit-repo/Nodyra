import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

type ToastTone = "success" | "error" | "info";

interface ToastAction {
  label: string;
  onClick: () => void;
}

interface Toast {
  id: number;
  tone: ToastTone;
  message: string;
  action?: ToastAction;
}

interface ToastContextValue {
  notify: (message: string, tone?: ToastTone, action?: ToastAction) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

const TOAST_TTL_MS: Record<ToastTone, number> = {
  error: 8000,
  success: 3600,
  info: 4200,
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Per-toast dismiss timers, so hovering can pause/resume them.
  const timers = useRef<Map<number, number>>(new Map());

  const dismiss = useCallback((id: number) => {
    const handle = timers.current.get(id);
    if (handle !== undefined) {
      window.clearTimeout(handle);
      timers.current.delete(id);
    }
    setToasts((current) => current.filter((t) => t.id !== id));
  }, []);

  const scheduleDismiss = useCallback(
    (id: number, tone: ToastTone) => {
      const handle = window.setTimeout(() => dismiss(id), TOAST_TTL_MS[tone]);
      timers.current.set(id, handle);
    },
    [dismiss],
  );

  const notify = useCallback(
    (message: string, tone: ToastTone = "info", action?: ToastAction) => {
      const id = Date.now() + Math.floor(Math.random() * 1000);
      setToasts((current) => [...current, { id, tone, message, action }].slice(-4));
      scheduleDismiss(id, tone);
    },
    [scheduleDismiss],
  );

  // Clear any outstanding timers on unmount.
  useEffect(() => {
    const map = timers.current;
    return () => {
      for (const handle of map.values()) window.clearTimeout(handle);
      map.clear();
    };
  }, []);

  const value = useMemo(() => ({ notify }), [notify]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="toast-stack" aria-live="polite" aria-atomic="false">
        {toasts.map((toast) => (
          <div
            className={`toast toast-${toast.tone}`}
            key={toast.id}
            role={toast.tone === "error" ? "alert" : undefined}
            // Pause the countdown while the pointer is over the toast so a user
            // reading a message isn't cut off; resume on leave.
            onMouseEnter={() => {
              const handle = timers.current.get(toast.id);
              if (handle !== undefined) {
                window.clearTimeout(handle);
                timers.current.delete(toast.id);
              }
            }}
            onMouseLeave={() => {
              if (!timers.current.has(toast.id)) scheduleDismiss(toast.id, toast.tone);
            }}
          >
            <span>{toast.message}</span>
            {toast.action && (
              <button
                type="button"
                className="toast-action"
                onClick={() => {
                  toast.action?.onClick();
                  dismiss(toast.id);
                }}
              >
                {toast.action.label}
              </button>
            )}
            <button
              type="button"
              className="toast-dismiss"
              aria-label="Dismiss notification"
              onClick={() => dismiss(toast.id)}
            >
              x
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const value = useContext(ToastContext);
  if (!value) return { notify: () => undefined };
  return value;
}
