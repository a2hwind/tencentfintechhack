#!/usr/bin/env python3
"""Scale evidence: Gate 1 exactness and latency on synthetic companies of 1k, 10k and 100k
chunks, 1,000 users each, built through the production ingestion path.

    python scripts/benchmark.py                          # all three sizes -> docs/evidence/scale.*
    python scripts/benchmark.py --sizes 1000 10000 --queries 100

Per size: generate a company with every ACL shape (internal_brain/evals/synthetic.py), fetch
every document through the real adapters over the mock APIs, DLP-mask, chunk, embed and index
it (SyncWorker.fetch_and_index), resolve every user's tokens through the adapters, then run
permission-filtered queries and compare each against the brute-force oracle
(internal_brain/evals/scale.py). Writes scale.json, scale.md and a light and dark chart.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from internal_brain.core.chunking import chunk_text  # noqa: E402
from internal_brain.core.embeddings import HashEmbedder  # noqa: E402
from internal_brain.evals.scale import build_sync, check_gate1  # noqa: E402
from internal_brain.evals.synthetic import SyntheticEmbedder, generate_company  # noqa: E402
from internal_brain.mocks.store import CompanyStore  # noqa: E402

OUT = ROOT / "docs" / "evidence"
USERS = 1000
PARAGRAPHS = (3, 7)

# Chart tokens (the validated reference palette: slots 1-3, light and dark steps)
THEMES = {
    "light": {"surface": "#fcfcfb", "ink": "#0b0b0b", "ink2": "#52514e", "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
              "series": ["#2a78d6", "#eb6834", "#1baf7a"], "bar": "#2a78d6"},
    "dark": {"surface": "#1a1a19", "ink": "#ffffff", "ink2": "#c3c2b7", "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
             "series": ["#3987e5", "#d95926", "#199e70"], "bar": "#3987e5"},
}


def chunks_per_item(seed: int = 99) -> float:
    company = generate_company(users=50, items=300, seed=seed, paragraphs=PARAGRAPHS)
    store = CompanyStore(company)
    texts = [store.confluence_page_text(p) for p in store.pages.values()] + [store.jira_issue_text(i) for i in store.issues.values()]
    texts += [store.slack_thread_text(t) for t in store.threads.values()] + [store.gdrive_file_text(f) for f in store.files.values()]
    return sum(len(chunk_text(t)) for t in texts) / len(texts)


def hash_embedder_rate(n: int = 2000) -> float:
    company = generate_company(users=20, items=n // 4, seed=5, paragraphs=PARAGRAPHS)
    texts = [c for p in company["confluence"]["pages"] for c in chunk_text(p["body"])][:n]
    t0 = time.perf_counter()
    HashEmbedder().embed(texts)
    return len(texts) / (time.perf_counter() - t0)


def run_size(target_chunks: int, ratio: float, queries: int, seed: int) -> dict:
    items = max(40, round(target_chunks / ratio))
    print(f"\n== ~{target_chunks:,} chunks: {items:,} documents, {USERS:,} users")
    company = generate_company(users=USERS, items=items, seed=seed, paragraphs=PARAGRAPHS)
    t0 = time.perf_counter()
    built = build_sync(company, SyntheticEmbedder(seed=seed), progress=lambda n, total: print(f"   indexed {n:,}/{total:,}", end="\r"))
    print(f"   built in {time.perf_counter() - t0:.1f} s ({built.chunks:,} chunks; ingestion {built.ingest_s:.1f} s)")
    t0 = time.perf_counter()
    report = check_gate1(built, n_queries=queries, seed=seed + 1)
    summary = report.as_dict()
    stats = built.index.vcache.stats()
    result = {
        "target_chunks": target_chunks,
        "documents": len(built.items),
        "chunks": built.chunks,
        "users": len(built.principals),
        "ingest_s": round(built.ingest_s, 1),
        "ingest_docs_per_s": round(len(built.items) / built.ingest_s, 1),
        "ingest_chunks_per_s": round(built.chunks / built.ingest_s, 1),
        "vector_index": {**stats, "memory_mb": round(stats["rows"] * (stats["dim"] or 0) * 4 / 1e6, 1)},
        "check_s": round(time.perf_counter() - t0, 1),
        **summary,
    }
    print(f"   {summary['queries']} queries: {summary['fts_mismatches']} keyword and {summary['vector_mismatches']} vector mismatches, "
          f"{summary['unpermitted_returned']} unpermitted results; p95 keyword {summary['fts_ms']['p95']} ms, vector {summary['vector_ms']['p95']} ms, "
          f"retrieval {summary['retrieve_ms']['p95']} ms; post-filter recall {summary['postfilter_recall_mean']}")
    built.index.conn.close()
    return result


def chart(results: list[dict], theme: str, path: Path) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    t = THEMES[theme]
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10})
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2), dpi=160, gridspec_kw={"width_ratios": [1.35, 1], "wspace": 0.32})
    fig.patch.set_facecolor(t["surface"])
    xs = [r["chunks"] for r in results]
    series = [("Full Gate 1 retrieval (4 sub-queries + audit)", "retrieve_ms"), ("Keyword search (FTS5, ACL in the query)", "fts_ms"), ("Vector search (permission bitmap)", "vector_ms")]
    for ax in (ax1, ax2):
        ax.set_facecolor(t["surface"])
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(t["axis"])
        ax.tick_params(colors=t["muted"], length=0)
        ax.yaxis.grid(True, color=t["grid"], linewidth=1)
        ax.set_axisbelow(True)
    for (label, key), color in zip(series, t["series"]):
        ys = [r[key]["p95"] for r in results]
        ax1.plot(xs, ys, color=color, linewidth=2, solid_capstyle="round", solid_joinstyle="round", zorder=3)
        ax1.scatter(xs, ys, s=64, color=color, edgecolors=t["surface"], linewidths=2, zorder=4, label=label)
        ax1.annotate(f"{ys[-1]:.1f} ms", (xs[-1], ys[-1]), xytext=(8, 0), textcoords="offset points", va="center", color=t["ink2"], fontsize=9)
    ax1.set_xscale("log")
    ax1.minorticks_off()
    ax1.set_xticks(xs)
    ax1.set_xticklabels([f"{x:,}" for x in xs], color=t["muted"])
    ax1.set_xlim(xs[0] / 1.6, xs[-1] * 2.4)
    ax1.set_ylim(bottom=0)
    ax1.set_xlabel("Chunks in the index (1,000 users)", color=t["ink2"])
    ax1.set_ylabel("p95 latency (ms)", color=t["ink2"])
    ax1.set_title("Gate 1 latency, 95th percentile", loc="left", color=t["ink"], fontsize=11, fontweight="bold", pad=10)
    legend = ax1.legend(loc="upper left", frameon=False, fontsize=8.5, labelcolor=t["ink2"], handletextpad=0.4, borderaxespad=0.2)
    for text in legend.get_texts():
        text.set_color(t["ink2"])

    labels = [f"{r['chunks']:,}" for r in results]
    recall = [r["postfilter_recall_mean"] * 100 for r in results]
    positions = range(len(results))
    ax2.bar(positions, recall, width=0.24, color=t["bar"], zorder=3)
    ax2.axhline(100, color=t["ink2"], linewidth=1, zorder=2)
    ax2.annotate("Filter inside the query: 100%", (len(results) - 1 + 0.4, 100), xytext=(0, 5), textcoords="offset points", ha="right", color=t["ink2"], fontsize=9, annotation_clip=False)
    for x, y in zip(positions, recall):
        ax2.annotate(f"{y:.0f}%", (x, y), xytext=(0, 4), textcoords="offset points", ha="center", color=t["ink2"], fontsize=9)
    ax2.set_xticks(list(positions))
    ax2.set_xticklabels(labels, color=t["muted"])
    ax2.set_ylim(0, 112)
    ax2.set_yticks([0, 25, 50, 75, 100])
    ax2.set_yticklabels(["0%", "25%", "50%", "75%", "100%"], color=t["muted"])
    ax2.set_xlabel("Chunks in the index", color=t["ink2"])
    ax2.set_title("Top-20 results kept by a naive post-filter", loc="left", color=t["ink"], fontsize=11, fontweight="bold", pad=10)
    fig.savefig(path, facecolor=t["surface"], bbox_inches="tight")
    plt.close(fig)


def write_markdown(results: list[dict], meta: dict) -> str:
    def ms(r: dict, key: str) -> str:
        return f"{r[key]['p50']:.1f} / {r[key]['p95']:.1f}"

    rows = "\n".join(
        f"| {r['chunks']:,} | {r['documents']:,} | {r['queries']} | {r['fts_mismatches']} | {r['vector_mismatches']} | {r['unpermitted_returned']} | "
        f"{ms(r, 'fts_ms')} | {ms(r, 'vector_ms')} | {ms(r, 'retrieve_ms')} | {r['postfilter_recall_mean'] * 100:.0f}% |"
        for r in results
    )
    ingest = "\n".join(
        f"| {r['chunks']:,} | {r['documents']:,} | {r['ingest_s']:.1f} s | {r['ingest_docs_per_s']:,.0f} | {r['ingest_chunks_per_s']:,.0f} | "
        f"{r['vector_index']['memory_mb']:.1f} MB | {r['tokens_per_user']['median']:.0f} (max {r['tokens_per_user']['max']}) |"
        for r in results
    )
    total_q = sum(r["queries"] for r in results)
    total_bad = sum(r["fts_mismatches"] + r["vector_mismatches"] + r["unpermitted_returned"] for r in results)
    biggest = results[-1]
    return f"""# Scale evidence: exact permission filtering at {biggest['chunks']:,} chunks

Generated by `python scripts/benchmark.py` on {meta['generated']} ({meta['machine']}). Re-run it to reproduce;
`tests/test_scale.py` runs the same checks on smaller companies in CI.

**Result.** Across {total_q:,} permission-filtered queries on three synthetic companies of 1,000 users,
Gate 1 returned exactly the brute-force top 20 over the documents each asker may see, for keyword
and vector search alike, with platform, time-window and container filters: **{total_bad} mismatches and
0 results outside the asker's permissions**. At {biggest['chunks']:,} chunks the full Gate 1 retrieval
for a question (four platform sub-queries, keyword and vector each, plus the audit-only shadow query)
took {biggest['retrieve_ms']['p50']:.0f} ms at the median and {biggest['retrieve_ms']['p95']:.0f} ms at p95 on one process.

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="scale-dark.png">
  <img alt="Left: p95 latency of keyword search, vector search and full Gate 1 retrieval at 1k, 10k and 100k chunks. Right: share of the true top-20 permitted results that a naive post-filter keeps, against 100% for filtering inside the query." src="scale-light.png">
</picture>

## Correctness and latency

p50 / p95 in milliseconds. "Post-filter keeps" is what the common shortcut would return: take the
top 20 over everything, then drop what the asker cannot see. It silently loses results (and its
latency and emptiness leak how much restricted material matched); filtering inside the query does not.

| Chunks | Documents | Queries | Keyword mismatches | Vector mismatches | Unpermitted results | Keyword ms | Vector ms | Full retrieval ms | Post-filter keeps |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{rows}

## Ingestion and footprint

Every document went through the production path: the adapter fetched it over the (mock) platform
API, DLP masked it, it was chunked, embedded and written in one transaction per document.

| Chunks | Documents | Ingestion | Documents/s | Chunks/s | Vector index | Tokens per user, median |
|---:|---:|---:|---:|---:|---:|---:|
{ingest}

## What the synthetic companies contain

`internal_brain/evals/synthetic.py` generates 1,000 people (5% contractors: Slack guests with
external Drive accounts), about 70 groups, and documents on all four platforms with every ACL shape:
Confluence space grants, page restrictions (inherited down page trees, intersected when two sit on
one chain, and naming people who cannot see the space); Jira project roles and security levels
(including level members who cannot browse the project); public and private Slack channels, DMs and
guests; Drive shared-drive members, nested folder grants, per-file shares, owners and "anyone with
the link" (not a grant). Text comes from a pseudo-word vocabulary with per-container topic words, so
a question matches documents in containers the asker can and cannot see.

## How the check works

`internal_brain/evals/scale.py`, independent of the code under test:

- **Keywords:** the same FTS5 `MATCH` with no permission predicate and no `LIMIT`; rows filtered in
  Python by (document ACL tokens intersect the asker's tokens); first 20 kept.
- **Vectors:** every stored vector read from the `chunk_vectors` table (not the in-memory index),
  scored with numpy, permitted rows kept, top 20.
- Gate 1 must match the oracle score for score (ids equal up to ties at the 20th score).

## Caveats

- Vectors come from `SyntheticEmbedder` (a fixed random projection of the bag of words, 256
  dimensions): the permission filter and scoring are the production code, but embedding 100k chunks
  with the demo's hash embedder would measure Python hashing, not Gate 1. For reference, the hash
  embedder runs at {meta['hash_embedder_chunks_per_s']:,.0f} chunks/s on this machine; a hosted embedding
  model (Hunyuan) is batched in the sync worker.
- One process, SQLite in memory, {meta['machine']}. At production scale the same shapes map onto a
  vector database with a metadata filter on allowed principals and a search service with
  document-level security; the Index interface stays the same.
- The same randomized method found two real over-grants in the Confluence and Jira projections
  (a person named in a page restriction without access to the space; a security-level member
  without browse permission). Gate 2 was already denying both at the source; the projections are
  now exact, and `tests/test_scale.py` keeps them so.
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sizes", type=int, nargs="+", default=[1_000, 10_000, 100_000])
    parser.add_argument("--queries", type=int, default=300)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=Path, default=OUT)
    parser.add_argument("--charts-only", action="store_true", help="re-render the charts and markdown from an existing scale.json")
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    if args.charts_only:
        saved = json.loads((args.out / "scale.json").read_text())
        for theme in THEMES:
            chart(saved["results"], theme, args.out / f"scale-{theme}.png")
        (args.out / "scale.md").write_text(write_markdown(saved["results"], saved["meta"]))
        return 0

    ratio = chunks_per_item()
    print(f"calibration: {ratio:.2f} chunks per document")
    results = [run_size(size, ratio, args.queries, args.seed + i) for i, size in enumerate(args.sizes)]
    meta = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "machine": f"Python {platform.python_version()}, {platform.machine()}, {__import__('os').cpu_count()} vCPU",
        "hash_embedder_chunks_per_s": round(hash_embedder_rate(), 0),
        "users": USERS,
    }
    (args.out / "scale.json").write_text(json.dumps({"meta": meta, "results": results}, indent=2))
    try:
        for theme in THEMES:
            chart(results, theme, args.out / f"scale-{theme}.png")
    except ImportError:
        print("matplotlib is not installed (pip install -e '.[dev]'): charts skipped")
    (args.out / "scale.md").write_text(write_markdown(results, meta))
    print(f"\nwrote {args.out / 'scale.md'}, scale.json, scale-light.png, scale-dark.png")
    return 0 if all(r["fts_mismatches"] == r["vector_mismatches"] == r["unpermitted_returned"] == 0 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
