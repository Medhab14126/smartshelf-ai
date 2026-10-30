import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/;

// Cafe staff read dates day-first (dd/mm/yyyy) whatever the browser's locale is.
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const iso = ISO_DATE.exec(value);
  if (iso) return `${iso[3]}/${iso[2]}/${iso[1]}`;
  return new Date(value).toLocaleDateString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric" });
}

/** Month spelled out ("4 May 2027"), for choosing between day/month-swapped readings. */
export function formatDateLong(value: string): string {
  const iso = ISO_DATE.exec(value);
  if (!iso) return value;
  return new Date(Date.UTC(+iso[1], +iso[2] - 1, +iso[3])).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", { day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

/** Parse a typed dd/mm/yyyy date (also dd-mm-yyyy, dd.mm.yyyy and 2-digit years) to ISO, or null if invalid. */
export function parseDayFirstDate(text: string): string | null {
  const match = /^\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{2}|\d{4})\s*$/.exec(text);
  if (!match) return null;
  const day = Number(match[1]);
  const month = Number(match[2]);
  const year = match[3].length === 2 ? 2000 + Number(match[3]) : Number(match[3]);
  const date = new Date(Date.UTC(year, month - 1, day));
  // Rejects 31/02 and similar instead of rolling over to the next month.
  if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 || date.getUTCDate() !== day) return null;
  return `${String(year).padStart(4, "0")}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

export const isIsoDate = (value: string) => ISO_DATE.test(value);

export function formatQty(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  return Number.isFinite(num) ? num.toLocaleString(undefined, { maximumFractionDigits: 3 }) : String(value);
}

export function formatMoney(value: string | number | null | undefined, currency?: string | null): string {
  if (value === null || value === undefined || value === "") return "—";
  const num = Number(value);
  if (!Number.isFinite(num)) return String(value);
  if (currency) {
    try {
      return num.toLocaleString(undefined, { style: "currency", currency });
    } catch {
      /* unknown currency code: fall through */
    }
  }
  return num.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function daysLabel(days: number | null | undefined): string {
  if (days === null || days === undefined) return "No date";
  if (days < 0) return `${Math.abs(days)} day${days === -1 ? "" : "s"} ago`;
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  return `In ${days} days`;
}

export function todayIso(): string {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
}
