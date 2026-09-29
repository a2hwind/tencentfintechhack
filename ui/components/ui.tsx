"use client";

import type { ReactNode } from "react";
import { platformLabel, pretty, shortHash } from "@/lib/format";

/** Upper-case platform badge: CONFLUENCE / JIRA / SLACK / DRIVE. */
export function PlatformBadge({ platform }: { platform: string | null | undefined }) {
  const key = platform ?? "";
  return <span className={`badge badge-platform ${key}`}>{platformLabel(platform)}</span>;
}

export function DecisionBadge({ value }: { value: "allow" | "deny" | "skipped" | string }) {
  if (value === "allow") return <span className="badge badge-allow">allow</span>;
  if (value === "deny") return <span className="badge badge-deny">deny</span>;
  return <span className="badge badge-muted">{value}</span>;
}

export function RoleBadges({ roles }: { roles: string[] }) {
  if (!roles.length) return null;
  return (
    <span className="role-badges">
      {roles.map((r) => (
        <span key={r} className={`role-badge ${r}`}>
          {r}
        </span>
      ))}
    </span>
  );
}

export function Collapsible({ title, children, defaultOpen = false, right }: { title: ReactNode; children: ReactNode; defaultOpen?: boolean; right?: ReactNode }) {
  return (
    <details className="collapsible" open={defaultOpen}>
      <summary>
        <span style={{ flex: 1 }}>{title}</span>
        {right}
      </summary>
      <div className="collapsible-body">{children}</div>
    </details>
  );
}

export function JsonBlock({ value }: { value: unknown }) {
  return (
    <pre className="json">
      <code>{pretty(value)}</code>
    </pre>
  );
}

export function Notice({ kind = "info", children }: { kind?: "info" | "error" | "ok" | "warn" | "plain"; children: ReactNode }) {
  const cls = kind === "plain" ? "notice" : `notice notice-${kind}`;
  return <div className={cls}>{children}</div>;
}

export function Hash({ value, length = 10 }: { value: string | null | undefined; length?: number }) {
  return (
    <span className="mono" title={value ?? undefined}>
      {shortHash(value, length)}
    </span>
  );
}

export function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="chip-mono" title={title}>
      {children}
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Spinner({ label = "Loading…" }: { label?: string }) {
  return (
    <span className="thinking">
      <span className="pulse" />
      {label}
    </span>
  );
}
