import type { ReactNode } from "react";

import { Clock, Info, Loader, XCircle } from "../icons";

type Variant = "loading" | "info" | "warning" | "danger";

type StatusMessageProps = {
  variant: Variant;
  heading: string;
  children?: ReactNode;
  /** Optional recovery actions rendered below the body. */
  actions?: ReactNode;
};

const TONE: Record<Variant, { wrap: string; icon: ReactNode; role: string }> = {
  loading: {
    wrap: "border-border bg-surface text-ink-muted",
    icon: <Loader className="h-4 w-4 shrink-0 animate-spin text-primary" />,
    role: "status",
  },
  info: {
    wrap: "border-warning/30 bg-warning/10 text-warning",
    icon: <Info className="h-4 w-4 shrink-0" />,
    role: "status",
  },
  warning: {
    wrap: "border-warning/30 bg-warning/10 text-warning",
    icon: <Clock className="h-4 w-4 shrink-0" />,
    role: "alert",
  },
  danger: {
    wrap: "border-danger/30 bg-danger/10 text-danger",
    icon: <XCircle className="h-4 w-4 shrink-0" />,
    role: "alert",
  },
};

/**
 * A transient app state (loading / no-route / rate-limited / error) rendered
 * as a consistent icon + heading + body(+actions) card rather than a bare
 * paragraph. Icon overrides let 429 (Clock) and 422 (Info/AlertCircle)
 * differ even when they share a tone.
 */
export default function StatusMessage({
  variant,
  heading,
  children,
  actions,
}: StatusMessageProps) {
  const tone = TONE[variant];

  return (
    <div
      role={tone.role}
      className={`flex items-start gap-2.5 rounded-lg border p-3 ${tone.wrap}`}
    >
      <span className="mt-0.5">{tone.icon}</span>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-semibold">{heading}</p>
        {children != null && (
          <p className="text-sm text-ink-muted">{children}</p>
        )}
        {actions != null && (
          <div className="flex flex-wrap gap-x-4 gap-y-1 pt-0.5">{actions}</div>
        )}
      </div>
    </div>
  );
}

/** A low-emphasis inline text button for recovery actions inside a message. */
export function InlineAction({
  onClick,
  children,
}: {
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="text-sm font-semibold text-primary underline decoration-primary/40 underline-offset-2 hover:decoration-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 rounded"
    >
      {children}
    </button>
  );
}
