"use client";

import { useState } from "react";
import { deleteItem, type IndexItem } from "@/lib/api";
import { fmtDateTime } from "@/lib/format";
import { Empty, PlatformBadge } from "@/components/ui";
import type { RunAction } from "./common";

export function IndexCard({ userId, items, run, busy }: { userId: string; items: IndexItem[]; run: RunAction; busy: boolean }) {
  const [deleteNotify, setDeleteNotify] = useState(true);
  const [confirmId, setConfirmId] = useState<string | null>(null);

  const doDelete = (itemId: string) => {
    setConfirmId(null);
    void run(`Delete ${itemId}${deleteNotify ? "" : " (no notify)"}`, () => deleteItem(userId, itemId, deleteNotify));
  };

  return (
    <div className="card">
      <div className="card-header">
        <h2>
          Index <span className="muted small" style={{ fontWeight: 400 }}>· {items.length} items</span>
        </h2>
        <label className="check" title="Unchecked simulates a missed deletion webhook: the item stays in the index until the next sync, and Gate 2 must deny it">
          <input type="checkbox" checked={deleteNotify} onChange={(e) => setDeleteNotify(e.target.checked)} />
          notify on delete
        </label>
      </div>
      {items.length === 0 ? (
        <Empty>The index is empty.</Empty>
      ) : (
        <div className="table-wrap table-scroll">
          <table className="table table-compact">
            <thead>
              <tr>
                <th>Item</th>
                <th>Platform</th>
                <th>Title</th>
                <th className="num">Ver.</th>
                <th>Modified</th>
                <th>Container</th>
                <th>Allowed principals</th>
                <th>Links</th>
                <th className="num">Chunks</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {items.map((it) => (
                <tr key={it.item_id}>
                  <td className="mono small nowrap">{it.item_id}</td>
                  <td>
                    <PlatformBadge platform={it.platform} />
                  </td>
                  <td className="truncate" style={{ maxWidth: 260 }} title={it.title}>
                    {it.title}
                  </td>
                  <td className="num">{it.version}</td>
                  <td className="nowrap">{fmtDateTime(it.last_modified)}</td>
                  <td className="mono small">{it.container ?? "—"}</td>
                  <td>
                    <span className="chips">
                      {it.allowed_principals.map((p) => (
                        <span key={p} className="chip-mono">
                          {p}
                        </span>
                      ))}
                      {it.allowed_principals.length === 0 && <span className="badge badge-deny">nobody</span>}
                    </span>
                  </td>
                  <td>
                    {it.links.length ? (
                      <span className="chips">
                        {it.links.map((l) => (
                          <span key={l} className="chip-mono">
                            {l}
                          </span>
                        ))}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="num">{it.chunks}</td>
                  <td className="nowrap">
                    {confirmId === it.item_id ? (
                      <span className="row" style={{ gap: 4, flexWrap: "nowrap" }}>
                        <button type="button" className="btn btn-sm btn-danger" disabled={busy} onClick={() => doDelete(it.item_id)}>
                          Confirm
                        </button>
                        <button type="button" className="btn btn-sm" onClick={() => setConfirmId(null)}>
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button type="button" className="btn btn-sm" disabled={busy} onClick={() => setConfirmId(it.item_id)} title="Delete this item at its source platform">
                        Delete
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
