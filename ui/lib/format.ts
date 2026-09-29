import type { AccessRow, Decision, Entry, EntryKind, Platform } from "./api";

const PLATFORM_LABELS: Record<string, string> = {
  confluence: "CONFLUENCE",
  jira: "JIRA",
  slack: "SLACK",
  gdrive: "DRIVE",
  directory: "DIRECTORY",
};

/** Upper-case badge label for a platform, e.g. gdrive -> DRIVE. */
export function platformLabel(platform: string | null | undefined): string {
  if (!platform) return "?";
  return PLATFORM_LABELS[platform] ?? platform.toUpperCase();
}

const PLATFORM_NAMES: Record<string, string> = {
  confluence: "Confluence",
  jira: "Jira",
  slack: "Slack",
  gdrive: "Drive",
  directory: "Directory",
};

/** Title-case product name for prose, e.g. gdrive -> Drive. */
export function platformName(platform: string | null | undefined): string {
  if (!platform) return "the source";
  return PLATFORM_NAMES[platform] ?? platform;
}

/** The platform prefix of a doc id such as `jira:DBM-42`. */
export function platformOf(doc: string): Platform | string {
  const idx = doc.indexOf(":");
  return idx > 0 ? doc.slice(0, idx) : doc;
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

/** Compact local timestamp, `YYYY-MM-DD HH:MM:SS`, for dense tables. */
export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" });
}

export function shortHash(hash: string | null | undefined, length = 10): string {
  if (!hash) return "—";
  return hash.length > length ? `${hash.slice(0, length)}…` : hash;
}

/** Local wall-clock time, `HH:MM:SS`, for live feeds. */
export function fmtClock(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${pad2(d.getHours())}:${pad2(d.getMinutes())}:${pad2(d.getSeconds())}`;
}

/** A measured duration: `0.28 ms`, `12 ms`, `1.4 s`. */
export function fmtMs(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms)) return "—";
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms >= 10_000 ? 0 : 1)} s`;
  if (ms >= 10) return `${Math.round(ms)} ms`;
  if (ms >= 1) return `${ms.toFixed(1)} ms`;
  return `${ms.toFixed(2)} ms`;
}

/** Exact byte counts with a thousands separator: `4,151 bytes`. */
export function fmtBytes(n: number): string {
  return `${n.toLocaleString("en-US")} byte${n === 1 ? "" : "s"}`;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

const REDACTION_LABELS: Record<string, string> = {
  card: "card number",
  nric: "NRIC/FIN",
  bank_account: "bank account",
  secret: "secret",
};

export function redactionLabel(kind: string): string {
  return REDACTION_LABELS[kind] ?? kind.replace(/_/g, " ");
}

export function sumCounts(counts: Record<string, number> | null | undefined): number {
  if (!counts) return 0;
  return Object.values(counts).reduce((a, b) => a + (Number.isFinite(b) ? b : 0), 0);
}

export function kindLabel(kind: EntryKind | string): string {
  switch (kind) {
    case "query":
      return "query";
    case "permission_event":
      return "permission";
    case "content_event":
      return "content";
    case "sync_event":
      return "sync";
    case "audit_read":
      return "audit read";
    case "source_open":
      return "source";
    case "alert":
      return "alert";
    default:
      return kind;
  }
}

export type SeverityLevel = "low" | "medium" | "high";

/** Narrow an unknown severity (from an entry's event) to a level, defaulting to low. */
export function severityOf(value: unknown): SeverityLevel {
  return value === "high" || value === "medium" ? value : "low";
}

/** The effective decision of one document: allowed only when both gates allow. */
export function decisionOf(d: Pick<Decision, "gate1" | "gate2">): "allow" | "deny" {
  return d.gate1 === "allow" && d.gate2 === "allow" ? "allow" : "deny";
}

export function countDecisions(decisions: Decision[]): { allow: number; deny: number } {
  let allow = 0;
  let deny = 0;
  for (const d of decisions) {
    if (decisionOf(d) === "allow") allow += 1;
    else deny += 1;
  }
  return { allow, deny };
}

export function isRefreshed(row: Pick<AccessRow, "refreshed">): boolean {
  return row.refreshed === true || row.refreshed === 1;
}

function str(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

/** One-line summary of an entry for the entries table. */
export function summarizeEntry(entry: Pick<Entry, "kind" | "query" | "event">): string {
  if (entry.kind === "query") return entry.query ?? "";
  const ev = entry.event ?? {};
  const get = (key: string): string => str(ev[key]);
  switch (entry.kind) {
    case "permission_event": {
      const target = get("channel") || get("item_id") || get("group") || "";
      const who = get("user") ? ` ${get("user")}` : "";
      const level = "level" in ev ? ` level=${get("level") || "none"}` : "";
      const notified = ev["notified"] === false ? " (webhook missed)" : "";
      return `${get("platform")} ${get("action")}${who} ${target}${level}${notified}`.replace(/\s+/g, " ").trim();
    }
    case "content_event": {
      const notified = ev["notified"] === false ? " (webhook missed)" : "";
      const version = "version" in ev ? ` v${get("version")}` : "";
      return `${get("platform")} ${get("action")} ${get("item_id")}${version}${notified}`.replace(/\s+/g, " ").trim();
    }
    case "sync_event": {
      const items = Array.isArray(ev["items"]) ? (ev["items"] as unknown[]).length : 0;
      return `${get("platform")} sync (${get("reason")}): ${get("updated") || 0} updated, ${get("deleted") || 0} deleted, ${items} items`;
    }
    case "audit_read": {
      const view = get("view");
      const parts: string[] = [`view=${view}`];
      for (const key of ["seq", "actor", "doc", "question", "returned"]) {
        if (key in ev && ev[key] !== null && ev[key] !== undefined) parts.push(`${key}=${get(key)}`);
      }
      if (ev["filter"] && typeof ev["filter"] === "object" && Object.keys(ev["filter"] as object).length > 0) {
        parts.push(`filter=${JSON.stringify(ev["filter"])}`);
      }
      return parts.join(" ");
    }
    case "source_open": {
      const available = ev["available"] === true;
      const reason = get("reason");
      return `${available ? "opened" : "unavailable"} ${get("doc")}${!available && reason ? ` (${reason})` : ""}`.trim();
    }
    case "alert": {
      const severity = get("severity");
      return `${severity ? `[${severity}] ` : ""}${get("title") || get("rule")}`;
    }
    default:
      return Object.keys(ev).length ? JSON.stringify(ev) : "";
  }
}

export function pretty(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}
