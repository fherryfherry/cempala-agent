export const DEFAULT_TIMEZONE = "Asia/Jakarta";

const ID_TZ_FORMATTERS = new Map<string, Intl.DateTimeFormat>();

function idIDFormatter(timezone: string): Intl.DateTimeFormat {
  let fmt = ID_TZ_FORMATTERS.get(timezone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("id-ID", {
      timeZone: timezone,
      dateStyle: "short",
      timeStyle: "short",
    });
    ID_TZ_FORMATTERS.set(timezone, fmt);
  }
  return fmt;
}

const BUBBLE_DATE_FORMATTERS = new Map<string, Intl.DateTimeFormat>();
const BUBBLE_TIME_FORMATTERS = new Map<string, Intl.DateTimeFormat>();
const BUBBLE_SHORT_FORMATTERS = new Map<string, Intl.DateTimeFormat>();

function bubbleDateFormatter(timezone: string): Intl.DateTimeFormat {
  let fmt = BUBBLE_DATE_FORMATTERS.get(timezone);
  if (!fmt) {
    // en-CA renders YYYY-MM-DD, which compares cleanly for the "is today" check.
    fmt = new Intl.DateTimeFormat("en-CA", {
      timeZone: timezone,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    });
    BUBBLE_DATE_FORMATTERS.set(timezone, fmt);
  }
  return fmt;
}

function bubbleTimeFormatter(timezone: string): Intl.DateTimeFormat {
  let fmt = BUBBLE_TIME_FORMATTERS.get(timezone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("id-ID", {
      timeZone: timezone,
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    });
    BUBBLE_TIME_FORMATTERS.set(timezone, fmt);
  }
  return fmt;
}

function bubbleShortFormatter(timezone: string): Intl.DateTimeFormat {
  let fmt = BUBBLE_SHORT_FORMATTERS.get(timezone);
  if (!fmt) {
    fmt = new Intl.DateTimeFormat("id-ID", {
      timeZone: timezone,
      day: "numeric",
      month: "short",
    });
    BUBBLE_SHORT_FORMATTERS.set(timezone, fmt);
  }
  return fmt;
}

/** Format a UTC ISO timestamp in the given IANA timezone (default: WIB).
 * Falls back to WIB when the timezone string is missing or invalid, so the UI
 * never breaks on a bad stored value. */
export function formatTimestamp(iso: string, timezone?: string | null): string {
  const tz = timezone || DEFAULT_TIMEZONE;
  try {
    return idIDFormatter(tz).format(new Date(iso));
  } catch {
    return idIDFormatter(DEFAULT_TIMEZONE).format(new Date(iso));
  }
}

/** Short timestamp: just the time for today, date + time otherwise — computed in
 * the given timezone, so "today" matches what the user sees (not the browser's
 * local zone). */
export function formatShortTime(isoDate: string | null, timezone?: string | null): string {
  if (!isoDate) return "";
  const tz = timezone || DEFAULT_TIMEZONE;
  const date = new Date(isoDate);
  try {
    const today = bubbleDateFormatter(tz).format(new Date());
    const thatDay = bubbleDateFormatter(tz).format(date);
    const time = bubbleTimeFormatter(tz).format(date);
    if (today === thatDay) return time;
    return `${bubbleShortFormatter(tz).format(date)}, ${time}`;
  } catch {
    return bubbleTimeFormatter(DEFAULT_TIMEZONE).format(date);
  }
}
