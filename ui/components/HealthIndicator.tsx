"use client";

import { useEffect, useState } from "react";
import { API_BASE, PLATFORMS, describeError, getHealth, type Health } from "@/lib/api";
import { platformName } from "@/lib/format";

const REFRESH_MS = 30_000;

/** "all mock", "all real", or the platforms that are not mocked, e.g. "Slack: real". */
export function connectorSummary(connectors: Health["connectors"]): string | null {
  if (!connectors) return null;
  const entries = PLATFORMS.map((p) => [p, connectors[p] ?? "mock"] as const);
  if (entries.every(([, mode]) => mode === "mock")) return "all mock";
  if (entries.every(([, mode]) => mode === "real")) return "all real";
  return entries
    .filter(([, mode]) => mode !== "mock")
    .map(([p, mode]) => `${platformName(p)}: ${mode}`)
    .join(" · ");
}

export function HealthIndicator() {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = () => {
      getHealth()
        .then((h) => {
          if (cancelled) return;
          setHealth(h);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(describeError(err));
        });
    };
    load();
    const timer = window.setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, []);

  if (error) {
    return (
      <div className="health" title={error}>
        <span className="health-dot bad" />
        <span>API unreachable ({API_BASE})</span>
      </div>
    );
  }
  if (!health) {
    return (
      <div className="health">
        <span className="health-dot" />
        <span>checking…</span>
      </div>
    );
  }
  const items = Object.values(health.sync.index.items).reduce((a, b) => a + b, 0);
  const connectors = connectorSummary(health.connectors);
  const connectorDetail = health.connectors ? PLATFORMS.map((p) => `${platformName(p)} ${health.connectors?.[p] ?? "mock"}`).join(", ") : "unknown";
  const title = [
    `API ${API_BASE}`,
    `planner: ${health.planner} · answerer: ${health.answerer}`,
    `embeddings: ${health.embeddings}`,
    `verifier: ${health.verifier}`,
    `connectors: ${connectorDetail}`,
    health.dlp ? `DLP ingest version ${health.dlp.ingest_version}` : null,
    health.vector_index ? `vector index: ${health.vector_index.rows} rows, ${health.vector_index.items} items, ${health.vector_index.principals} principals, dim ${health.vector_index.dim}` : null,
    `audit entries: ${health.audit.entries}`,
    `sync ${health.sync.paused ? "paused" : "running"}, ${health.sync.runs} runs`,
  ]
    .filter(Boolean)
    .join("\n");
  return (
    <div className="health" title={title}>
      <span className={`health-dot ${health.status === "ok" ? "ok" : "bad"}`} />
      <span>
        <span className="health-models">
          planner <b>{health.planner}</b> · answerer <b>{health.answerer}</b> ·{" "}
        </span>
        <b>{items}</b> items
        {connectors ? (
          <>
            {" "}
            · connectors <b>{connectors}</b>
          </>
        ) : null}
        {health.sync.paused ? (
          <span className="badge badge-amber" style={{ marginLeft: 6 }}>
            sync paused
          </span>
        ) : null}
      </span>
    </div>
  );
}
