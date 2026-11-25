import type { ReactNode } from "react";
import type { ArmId, Classification, Disposition } from "../types";
import { formatMoney, humanize } from "../format";
import { AMOUNT_MISMATCH_LABEL, type AmountMismatch } from "../derive";

type IconName = "check" | "x" | "stop" | "pause" | "shield" | "alert" | "clock" | "arrow" | "play" | "replay" | "next" | "prev" | "dot";

const PATHS: Record<IconName, ReactNode> = {
  check: <path d="M4 12.5l5 5L20 6.5" />,
  x: <path d="M6 6l12 12M18 6L6 18" />,
  stop: <><circle cx="12" cy="12" r="8.5" /><path d="M7.5 12h9" /></>,
  pause: <><path d="M9 6v12M15 6v12" /></>,
  shield: <path d="M12 3l7 3v5.5c0 4.2-2.9 7.6-7 9.5-4.1-1.9-7-5.3-7-9.5V6l7-3z" />,
  alert: <><path d="M12 4l9 16H3L12 4z" /><path d="M12 10v4.5M12 17.6v.01" /></>,
  clock: <><circle cx="12" cy="12" r="8.5" /><path d="M12 7v5l3.2 2" /></>,
  arrow: <path d="M4 12h15M13 6l6 6-6 6" />,
  play: <path d="M8 5.5v13l11-6.5-11-6.5z" />,
  replay: <><path d="M4 12a8 8 0 1 0 2.6-5.9" /><path d="M4 4v4.5h4.5" /></>,
  next: <path d="M6 5.5v13l9-6.5-9-6.5zM18 5.5v13" />,
  prev: <path d="M18 5.5v13l-9-6.5 9-6.5zM6 5.5v13" />,
  dot: <circle cx="12" cy="12" r="3" />,
};

export function Icon({ name, size = 16 }: { name: IconName; size?: number }) {
  return (
    <svg
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

const CLASS_META: Record<Classification, { label: string; icon: IconName; tone: string }> = {
  correct_payment: { label: "Correct payment", icon: "check", tone: "good" },
  correct_stop: { label: "Correct stop", icon: "shield", tone: "good" },
  false_stop: { label: "False stop", icon: "pause", tone: "warn" },
  wrong_payment: { label: "Wrong payment", icon: "x", tone: "bad" },
};

export function ClassBadge({ value, compact = false }: { value: Classification; compact?: boolean }) {
  const m = CLASS_META[value];
  return (
    <span className={`badge badge--${m.tone}`} data-class={value}>
      <Icon name={m.icon} size={compact ? 13 : 14} />
      {m.label}
    </span>
  );
}

const SHORT: Record<Classification, string> = {
  correct_payment: "paid ok",
  correct_stop: "stopped ok",
  false_stop: "false stop",
  wrong_payment: "wrong pay",
};
export const classShort = (c: Classification) => SHORT[c];
export const classLabel = (c: Classification) => CLASS_META[c].label;
export const classTone = (c: Classification) => CLASS_META[c].tone;
export const classIcon = (c: Classification) => CLASS_META[c].icon;

const DISPOSITION_LABEL: Record<Disposition, string> = {
  paid: "Paid",
  denied: "Denied",
  escalated: "Escalated",
  blocked: "Blocked",
  held_for_review: "Held for review",
};
export const dispositionLabel = (d: Disposition) => DISPOSITION_LABEL[d] ?? humanize(d);

export function ArmMark({ arm, size = "md" }: { arm: ArmId; size?: "sm" | "md" | "lg" }) {
  return (
    <span className={`armmark armmark--${size} armmark--${arm}`} aria-hidden="true">
      {arm}
    </span>
  );
}

/** Marker for a paid refund whose amount or currency differs from the ticket's request. */
export function AmountFlag({ mismatch }: { mismatch: AmountMismatch }) {
  const title = `Paid ${formatMoney(mismatch.paidMinor, mismatch.paidCurrency)}; the ticket requested ${formatMoney(mismatch.requestedMinor, mismatch.requestedCurrency)}`;
  return (
    <span className="chip chip--warn amountflag" data-testid="amount-mismatch" title={title}>
      <Icon name="alert" size={12} />
      {AMOUNT_MISMATCH_LABEL}
    </span>
  );
}

export function Chip({ children, tone = "neutral", title }: { children: ReactNode; tone?: string; title?: string }) {
  return (
    <span className={`chip chip--${tone}`} title={title}>
      {children}
    </span>
  );
}

export const CONTEXT_LABEL: Record<string, string> = {
  live_state: "sees the live T1 state",
  memory_note: "sees only its own T0 note",
  frozen_state: "sees the frozen T0 state",
};
