/** Money arrives as integer minor units plus an ISO-4217 currency; never as floats. */
export function minorExponent(currency: string): number {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions()
      .maximumFractionDigits ?? 2;
  } catch {
    return 2;
  }
}

export function formatMoney(minor: number | null | undefined, currency: string): string {
  if (minor === null || minor === undefined) return "n/a";
  const exp = minorExponent(currency);
  const value = minor / 10 ** exp;
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      minimumFractionDigits: exp,
      maximumFractionDigits: exp,
    }).format(value);
  } catch {
    return `${value.toFixed(exp)} ${currency}`;
  }
}

export function formatInt(n: number | null | undefined): string {
  if (n === null || n === undefined) return "n/a";
  return new Intl.NumberFormat("en-US").format(n);
}

/** "2026-10-12T14:00:00Z" -> "12 Oct 2026, 14:00 UTC" (no locale or wall-clock dependence). */
export function formatInstant(iso: string | null | undefined): string {
  if (!iso) return "n/a";
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (!m) return iso;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${Number(m[3])} ${months[Number(m[2]) - 1]} ${m[1]}, ${m[4]}:${m[5]} UTC`;
}

export function humanize(token: string): string {
  return token.replace(/_/g, " ");
}

export function compactJson(v: unknown): string {
  if (v === undefined) return "n/a";
  if (v === null) return "null";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** C1: the money figure is each scenario's harm, not the amount paid (an overpayment counts only its excess). */
export const HARM_NOTE =
  "Harm is what each wrong payment cost under the scenario's ground truth, not the amount paid: an overpayment counts only the excess.";
