/**
 * Typed client for the Internal Brain API.
 *
 * Every function that touches an identity-scoped endpoint takes the acting user's id
 * as its first argument and sends it as `X-User-Id`. `/users` and `/health` are open.
 */

import { readEventStream } from "./sse";

export const API_BASE: string = (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000").replace(/\/+$/, "");

// ---------------------------------------------------------------------------
// Shared types
// ---------------------------------------------------------------------------
export type Platform = "confluence" | "jira" | "slack" | "gdrive";
export const PLATFORMS: Platform[] = ["confluence", "jira", "slack", "gdrive"];

export interface User {
  id: string;
  name: string;
  title: string;
  roles: string[];
}

export interface SyncPlatform {
  platform: string;
  cursor: string | null;
  last_run: string | null;
  items_synced: number;
}

export interface IndexStats {
  items: Record<string, number>;
  chunks: number;
}

export interface SyncStatus {
  paused: boolean;
  interval_s: number;
  runs: number;
  last_run: string | null;
  pending_events: number;
  platforms: SyncPlatform[];
  index: IndexStats;
}

export interface AuditHeadRef {
  seq: number;
  ts: string;
  entry_hash: string;
}

export interface EntitlementCacheStats {
  hits: number;
  misses: number;
  invalidations: number;
}

/** How a platform is connected: the in-process mock, or the real service. */
export type ConnectorMode = "mock" | "real";

export interface VectorIndexStats {
  rows: number;
  items: number;
  principals: number;
  dim: number;
}

export interface Health {
  status: string;
  version?: string;
  started_at?: string | null;
  planner: string;
  answerer: string;
  embeddings: string;
  verifier: string;
  no_result_min_latency_ms?: number;
  sync: SyncStatus;
  audit: {
    entries: number;
    head: AuditHeadRef | null;
    key_id: string;
    checkpoint_every: number;
  };
  entitlement_cache: EntitlementCacheStats;
  /** Present on newer APIs. */
  dlp?: { ingest_version: string };
  /** Connector mode per platform; values other than mock/real are shown as-is. */
  connectors?: Partial<Record<Platform, ConnectorMode | string>>;
  vector_index?: VectorIndexStats;
  /** SLACK_MODE=real: the workspace connection. */
  slack?: SlackStatus;
}

export interface SlackStatus {
  mode: string;
  team?: { id: string; name?: string | null; url?: string | null; bot_user_id?: string | null } | null;
  threads_tracked?: number;
  calls?: number;
  rate_limited?: number;
  waited_s?: number;
  webhook?: boolean;
  last_event?: string | null;
  mapped_users?: string[];
}

export interface Me {
  identity: { id: string; name: string; email: string; roles: string[]; title: string };
  principals: string[];
  platform_user_ids: Record<string, string>;
  resolved_at: string;
}

// ---- /ask ------------------------------------------------------------------
export interface Citation {
  doc: string;
  platform: Platform;
  title: string;
  url: string | null;
  updated: string | null;
  /** Chunk ids of this document that went into the prompt. */
  chunks: string[];
}

export interface Provenance {
  doc: string;
  rule: string;
  verified_at: string;
  version: number | null;
}

/** The passage of one cited chunk that supports an answer sentence (an exact substring of the chunk's text). */
export interface EvidenceQuote {
  doc: string;
  chunk: string;
  quote: string;
}

export interface AnswerSentence {
  text: string;
  /** Doc ids cited by this sentence, in order. */
  citations: string[];
  evidence: EvidenceQuote[];
}

export interface AskResponse {
  answer: string;
  citations: Citation[];
  provenance: Provenance[];
  no_result: boolean;
  /** The answer sentence by sentence with evidence; empty for the uniform no-result. */
  sentences: AnswerSentence[];
}

/** An /ask response plus what the transport saw: the X-Audit-Seq header, the raw body and the round trip. */
export interface AskResult extends AskResponse {
  audit_seq: number | null;
  /** The response body exactly as received, for byte-level comparison. */
  raw: string;
  /** Size of the raw body in bytes (UTF-8). */
  bytes: number;
  /** Round trip measured in the browser, request to last byte, in ms. */
  elapsed_ms: number;
}

// ---- /sources --------------------------------------------------------------
export interface SourceChunk {
  chunk: string;
  text: string;
}

/**
 * GET /sources/{doc}: a cited document re-checked at open (Gate 1 and Gate 2, live).
 * Unavailable is one uniform shape: `available: false`, the message, nulls and no chunks.
 */
export interface SourceView {
  available: boolean;
  message: string | null;
  doc: string | null;
  platform: Platform | null;
  title: string | null;
  url: string | null;
  version: number | null;
  updated: string | null;
  rule: string | null;
  verified_at: string | null;
  refreshed: boolean;
  chunks: SourceChunk[];
}

export interface SourceOpenResult {
  view: SourceView;
  /** Opening a source is itself an audit entry. */
  audit_seq: number | null;
}

// ---- audit -----------------------------------------------------------------
export type Gate1 = "allow" | "deny";
export type Gate2 = "allow" | "deny" | "skipped";
export type EntryKind = "query" | "permission_event" | "content_event" | "sync_event" | "audit_read" | "source_open" | "alert";
export const ENTRY_KINDS: EntryKind[] = ["query", "source_open", "alert", "permission_event", "content_event", "sync_event", "audit_read"];

/** Pipeline stages timed on query entries, in execution order. */
export type TimingStage = "entitlements" | "plan" | "gate1" | "expand" | "gate2" | "assemble" | "answer" | "guard";
export const TIMING_STAGES: TimingStage[] = ["entitlements", "plan", "gate1", "expand", "gate2", "assemble", "answer", "guard"];
export type StageTimings = Partial<Record<TimingStage, number>>;

export interface Decision {
  doc: string;
  platform: Platform;
  gate1: Gate1;
  gate2: Gate2;
  rule: string;
  /** The token that passed Gate 1; kept when Gate 2 then denied (a stale entitlement). Absent on older entries. */
  gate1_rule?: string | null;
  version: number | null;
  refreshed: boolean;
  source: "retrieval" | "expansion";
  container: string | null;
}

export interface Entry {
  seq: number;
  ts: string;
  kind: EntryKind;
  actor: string;
  query: string | null;
  outcome: "answered" | "no_result" | null;
  decisions: Decision[];
  sent_to_model: string[];
  event: Record<string, unknown> | null;
  entry_hash: string;
}

export interface SentChunk {
  chunk: string;
  sha256: string;
}

export interface GuardStats {
  citations_rejected: number;
  claims_stripped: number;
  unsupported_claims: number;
  events: string[];
  /** DLP masks applied to the model's output, by type (card, nric, bank_account, secret). */
  redactions?: Record<string, number>;
}

export interface FullEntry {
  seq: number;
  ts: string;
  kind: EntryKind;
  actor: { id: string; principals: string[] };
  query: string | null;
  plan: Record<string, unknown> | null;
  decisions: Decision[];
  sent_to_model: SentChunk[];
  answer: string | null;
  guard: GuardStats | null;
  outcome: "answered" | "no_result" | null;
  latency_ms: number | null;
  model: string | null;
  /** Measured per pipeline stage (query entries on newer APIs). */
  timings_ms?: StageTimings | null;
  event: Record<string, unknown> | null;
  prev_hash: string;
  entry_hash: string;
}

export interface EntriesFilter {
  actor?: string;
  kind?: string;
  platform?: string;
  container?: string;
  doc?: string;
  decision?: "allow" | "deny";
  days?: number;
  limit?: number;
}

export interface EntriesResponse {
  filter: Record<string, unknown>;
  entries: Entry[];
  read_logged_as: number;
}

export interface EntryResponse {
  entry: FullEntry;
  read_logged_as: number;
}

/** One row of the audit_decisions index, as the canned views return it. */
export interface AccessRow {
  seq: number;
  ts: string;
  actor_id: string;
  doc: string;
  platform: Platform;
  container: string | null;
  gate1: Gate1;
  gate2: Gate2;
  rule: string;
  decision: "allow" | "deny";
  refreshed: boolean | number;
  /** Which kind of entry made the decision (newer APIs): a query, or opening the source from a citation. */
  kind?: "query" | "source_open";
}

export interface DocumentAccessSummary {
  doc: string;
  platform: Platform;
  container: string | null;
  allowed: number;
  denied: number;
  last_seen: string;
}

export interface UserAccessResponse {
  actor: string;
  days: number;
  container: string | null;
  queries: Entry[];
  documents: DocumentAccessSummary[];
  read_logged_as: number;
}

export interface DocAccessResponse {
  doc: string;
  accesses: AccessRow[];
  read_logged_as: number;
}

export interface DenialsResponse {
  actor: string;
  denials: AccessRow[];
  read_logged_as: number;
}

export interface NlFilter {
  actor?: string;
  doc?: string;
  container?: string;
  platform?: string;
  decision?: string;
  days?: number;
  kind?: string;
}

export interface NlResponse {
  question: string;
  filter: NlFilter;
  entries: Entry[];
  read_logged_as: number;
}

export interface VerifyReport {
  ok: boolean;
  entries: number;
  checkpoints: number;
  checkpoints_verified: number;
  first_broken_seq: number | null;
  gap_after_seq: number | null;
  bad_checkpoint_seq: number | null;
  head_hash: string | null;
  problems: string[];
}

export interface Checkpoint {
  seq: number;
  entry_hash: string;
  ts: string;
  key_id: string;
  signature: string;
}

export interface AuditHead {
  head: AuditHeadRef | null;
  entries: number;
  /** The most recent checkpoints (the API returns the last five). */
  checkpoints: Checkpoint[];
  key_id: string;
  public_key: string;
}

export interface ContainerInfo {
  container: string;
  platform: string;
  label: string | null;
  aliases: string[];
}

// ---- alerts ----------------------------------------------------------------
export type Severity = "low" | "medium" | "high";
export const SEVERITIES: Severity[] = ["low", "medium", "high"];
export type AlertRule = "blocked_burst" | "container_probe" | "retry_after_revocation" | "source_probe" | "model_citation_rejected" | "sensitive_data_at_rest";

export interface AlertSubject {
  type: "user" | "document";
  id: string;
}

/** An alert raised by the deterministic rules over the trail; it is itself an entry (`seq`) in the chain. */
export interface AuditAlert {
  seq: number;
  ts: string;
  entry_hash: string;
  rule: AlertRule | string;
  severity: Severity;
  title: string;
  detail: string;
  subject: AlertSubject;
  /** Entries that triggered the alert. */
  evidence_seqs: number[];
  key: string;
  container?: string | null;
  containers?: string[];
  doc?: string;
  /** Sensitive-data findings by type (sensitive_data_at_rest); values are never recorded. */
  counts?: Record<string, number>;
  version?: number;
}

export interface AlertsFilter {
  days?: number;
  limit?: number;
  min_severity?: Severity;
}

export interface AlertsResponse {
  alerts: AuditAlert[];
  read_logged_as: number;
}

// ---- live stream -----------------------------------------------------------
/** The live-tail shape of an entry; the full entry is one GET /audit/entries/{seq} away. */
export interface StreamEntry {
  seq: number;
  ts: string;
  kind: EntryKind;
  actor: string;
  query: string | null;
  outcome: "answered" | "no_result" | null;
  allow: number;
  deny: number;
  refreshed: boolean;
  revoked: boolean;
  latency_ms: number | null;
  redactions: Record<string, number>;
  citations_rejected: number;
  event: Record<string, unknown> | null;
  prev_hash: string;
  entry_hash: string;
}

export interface StreamHello {
  /** Opening the stream is itself an audit entry. */
  read_logged_as: number;
  head: AuditHeadRef | null;
}

export type StreamStatus = "connecting" | "open" | "retrying" | "closed";

export interface StreamOptions {
  /** Entries after this sequence number are sent (backlog first, then live). */
  since?: number;
  /** Include the log's own read entries (off by default: a console watching the chain does not watch itself). */
  includeReads?: boolean;
  signal: AbortSignal;
  onEntry: (entry: StreamEntry) => void;
  onHello?: (hello: StreamHello) => void;
  /** A connection failed; `retryInMs` is null when the stream gives up (authorization errors). */
  onError?: (message: string, retryInMs: number | null) => void;
  onStatus?: (status: StreamStatus) => void;
  /** First reconnect delay; it doubles up to `maxBackoffMs`. Opening a stream is an audit entry, so never retry in a tight loop. */
  minBackoffMs?: number;
  maxBackoffMs?: number;
}

// ---- admin -----------------------------------------------------------------
export interface SnapshotUser {
  id: string;
  name: string;
  title: string;
  roles: string[];
  platform_ids: Record<string, string>;
  slack_guest: boolean;
}

export interface Principals {
  users: string[];
  groups: string[];
}

export interface ConfluenceSpace {
  key: string;
  name: string;
  read: Principals;
}

export interface ConfluencePage {
  id: string;
  space: string;
  title: string;
  version: number;
  last_modified: string;
  restrictions: Principals | null;
  deleted: boolean;
}

export interface JiraProject {
  key: string;
  name: string;
  roles: Record<string, string[]>;
  security_levels: Record<string, string[]>;
}

export interface JiraIssue {
  key: string;
  project: string;
  summary: string;
  status: string;
  security_level: string | null;
  version: number;
  updated: string;
  deleted: boolean;
}

export interface SlackChannel {
  id: string;
  name: string;
  private: boolean;
  is_dm: boolean;
  members: string[];
}

export interface SlackThread {
  item_id: string;
  channel: string;
  ref: string;
  title: string;
  version: number;
  last_modified: string;
  deleted: boolean;
}

export interface Drive {
  id: string;
  name: string;
  members: Principals;
}

export interface DriveFolder {
  id: string;
  name: string;
  drive: string;
  permissions: Principals;
}

export interface DriveFile {
  id: string;
  name: string;
  drive: string;
  folder: string | null;
  version: number;
  modified: string;
  shared_with: string[];
  anyone_with_link: boolean;
  deleted: boolean;
}

export interface Snapshot {
  users: SnapshotUser[];
  groups: Record<string, string[]>;
  confluence: { spaces: ConfluenceSpace[]; pages: ConfluencePage[] };
  jira: { projects: JiraProject[]; issues: JiraIssue[] };
  slack: { channels: SlackChannel[]; threads: SlackThread[] };
  gdrive: { drives: Drive[]; folders: DriveFolder[]; files: DriveFile[] };
}

export interface PlatformEvent {
  kind: string;
  platform: string;
  payload: Record<string, unknown>;
  ts: string;
}

export interface AdminState {
  company: Snapshot;
  sync: SyncStatus;
  entitlement_cache: EntitlementCacheStats;
  events: PlatformEvent[];
}

export interface IndexItem {
  item_id: string;
  platform: Platform;
  title: string;
  version: number;
  last_modified: string;
  container: string | null;
  allowed_principals: string[];
  links: string[];
  chunks: number;
}

export interface MembershipChange {
  user_id: string;
  member: boolean;
  notify: boolean;
}

export interface MembershipResult {
  channel: string;
  name: string;
  members: string[];
  event_delivered: boolean;
}

export interface ContentEdit {
  append?: string;
  body?: string;
  notify: boolean;
}

export interface PageEditResult {
  item_id: string;
  version: number;
  indexed_version: number | null;
  event_delivered: boolean;
}

export interface RestrictionsChange {
  users: string[];
  groups: string[];
  notify: boolean;
}

export interface RestrictionsResult {
  item_id: string;
  version: number;
  restrictions: Principals;
}

export interface CommentIn {
  author: string;
  text: string;
  notify: boolean;
}

export interface SecurityLevelChange {
  level: string | null;
  notify: boolean;
}

export interface ShareChange {
  user_id: string;
  share: boolean;
  notify: boolean;
}

export interface GroupChange {
  user_id: string;
  member: boolean;
  notify: boolean;
}

export interface ItemResult {
  item_id: string;
  version: number;
}

export interface SecurityLevelResult extends ItemResult {
  security_level: string | null;
}

export interface ShareResult extends ItemResult {
  shared_with: string[];
}

export interface GroupResult {
  group: string;
  members: string[];
}

export interface DeleteResult {
  item_id: string;
  deleted: boolean;
  indexed: boolean;
}

export interface SyncRunResult {
  summary: Record<string, unknown>;
  status: SyncStatus;
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------
export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;

  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

type QueryValue = string | number | boolean | null | undefined;

interface RequestOptions {
  method?: "GET" | "POST" | "DELETE";
  userId?: string;
  body?: unknown;
  query?: Record<string, QueryValue>;
}

interface ApiReply<T> {
  data: T;
  headers: Headers;
  /** The body exactly as received. */
  raw: string;
}

function buildUrl(path: string, query?: Record<string, QueryValue>): string {
  const params = new URLSearchParams();
  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value === undefined || value === null || value === "") continue;
      params.set(key, String(value));
    }
  }
  const qs = params.toString();
  return `${API_BASE}${path}${qs ? `?${qs}` : ""}`;
}

async function readDetail(res: Response): Promise<string> {
  try {
    const parsed: unknown = await res.json();
    if (parsed && typeof parsed === "object" && "detail" in parsed) {
      const detail = (parsed as { detail: unknown }).detail;
      return typeof detail === "string" ? detail : JSON.stringify(detail);
    }
    return JSON.stringify(parsed);
  } catch {
    return res.statusText || `HTTP ${res.status}`;
  }
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<ApiReply<T>> {
  const headers: Record<string, string> = { Accept: "application/json" };
  if (options.userId) headers["X-User-Id"] = options.userId;
  if (options.body !== undefined) headers["Content-Type"] = "application/json";

  let res: Response;
  try {
    res = await fetch(buildUrl(path, options.query), {
      method: options.method ?? "GET",
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}`);
  }
  if (!res.ok) {
    throw new ApiError(res.status, await readDetail(res));
  }
  const raw = await res.text();
  let data: T;
  try {
    data = JSON.parse(raw) as T;
  } catch {
    throw new ApiError(res.status, "The API returned a body that is not JSON");
  }
  return { data, headers: res.headers, raw };
}

function auditSeqOf(headers: Headers): number | null {
  const raw = headers.get("X-Audit-Seq");
  const seq = raw !== null && raw !== "" ? Number(raw) : NaN;
  return Number.isFinite(seq) ? seq : null;
}

function utf8Length(text: string): number {
  return new TextEncoder().encode(text).length;
}

function now(): number {
  return typeof performance !== "undefined" ? performance.now() : Date.now();
}

function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const done = () => {
      window.clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = window.setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

async function call<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return (await request<T>(path, options)).data;
}

/** A human-readable message for any error thrown by this module. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return err.detail;
    if (err.status === 401) return `No identity: the API does not recognise the selected user (${err.detail}).`;
    if (err.status === 403) return `Not allowed: ${err.detail}.`;
    if (err.status === 404) return `Not found: ${err.detail}.`;
    return `HTTP ${err.status}: ${err.detail}`;
  }
  if (err instanceof Error) return err.message;
  return String(err);
}

// ---------------------------------------------------------------------------
// Open endpoints
// ---------------------------------------------------------------------------
export function getUsers(): Promise<User[]> {
  return call<User[]>("/users");
}

export function getHealth(): Promise<Health> {
  return call<Health>("/health");
}

// ---------------------------------------------------------------------------
// Identity-scoped: ask
// ---------------------------------------------------------------------------
export function getMe(userId: string): Promise<Me> {
  return call<Me>("/me", { userId });
}

export async function ask(userId: string, question: string): Promise<AskResult> {
  const started = now();
  const { data, headers, raw } = await request<AskResponse>("/ask", { method: "POST", userId, body: { question } });
  const elapsed = now() - started;
  return {
    ...data,
    // Older APIs predate these fields; the renderers fall back to the answer's [doc:…] tags.
    citations: (data.citations ?? []).map((c) => ({ ...c, chunks: Array.isArray(c.chunks) ? c.chunks : [] })),
    sentences: Array.isArray(data.sentences) ? data.sentences : [],
    audit_seq: auditSeqOf(headers),
    raw,
    bytes: utf8Length(raw),
    elapsed_ms: Math.round(elapsed),
  };
}

/**
 * Open a cited document as `userId`. Both gates are re-checked live at open and the open is logged.
 * Restricted, revoked, deleted and unknown documents all return the same uniform unavailable view.
 */
export async function openSource(userId: string, doc: string): Promise<SourceOpenResult> {
  const { data, headers } = await request<SourceView>(`/sources/${encodeURIComponent(doc)}`, { userId });
  return { view: { ...data, chunks: Array.isArray(data.chunks) ? data.chunks : [] }, audit_seq: auditSeqOf(headers) };
}

// ---------------------------------------------------------------------------
// Audit (compliance role)
// ---------------------------------------------------------------------------
export function getAuditEntries(userId: string, filter: EntriesFilter = {}): Promise<EntriesResponse> {
  return call<EntriesResponse>("/audit/entries", { userId, query: { ...filter } });
}

export function getAuditEntry(userId: string, seq: number): Promise<EntryResponse> {
  return call<EntryResponse>(`/audit/entries/${seq}`, { userId });
}

export function getUserAccess(userId: string, actor: string, days = 30, container?: string): Promise<UserAccessResponse> {
  return call<UserAccessResponse>("/audit/views/user-access", { userId, query: { actor, days, container } });
}

export function getDocAccess(userId: string, doc: string, days?: number): Promise<DocAccessResponse> {
  return call<DocAccessResponse>("/audit/views/doc-access", { userId, query: { doc, days } });
}

export function getDenials(userId: string, actor: string, days?: number): Promise<DenialsResponse> {
  return call<DenialsResponse>("/audit/views/denials", { userId, query: { actor, days } });
}

export function auditNaturalLanguage(userId: string, q: string): Promise<NlResponse> {
  return call<NlResponse>("/audit/nl", { userId, query: { q } });
}

export function verifyAudit(userId: string): Promise<VerifyReport> {
  return call<VerifyReport>("/audit/verify", { userId });
}

export function getAuditHead(userId: string): Promise<AuditHead> {
  return call<AuditHead>("/audit/head", { userId });
}

export function getAuditContainers(userId: string): Promise<ContainerInfo[]> {
  return call<ContainerInfo[]>("/audit/containers", { userId });
}

/** Insider-threat and data-hygiene alerts, newest first. Reading them is logged. */
export function getAlerts(userId: string, filter: AlertsFilter = {}): Promise<AlertsResponse> {
  return call<AlertsResponse>("/audit/alerts", { userId, query: { ...filter } });
}

function isStreamEntry(value: unknown): value is StreamEntry {
  return typeof value === "object" && value !== null && typeof (value as { seq?: unknown }).seq === "number" && typeof (value as { kind?: unknown }).kind === "string";
}

function isStreamHello(value: unknown): value is StreamHello {
  return typeof value === "object" && value !== null && typeof (value as { read_logged_as?: unknown }).read_logged_as === "number";
}

/** Thrown inside the stream reader to reconnect at once (the log was replaced). */
class StreamRestart extends Error {}

/**
 * Tail the audit chain as server-sent events: GET /audit/stream with the identity header, which
 * EventSource cannot send, so this reads the stream with fetch. Reconnects with exponential backoff
 * from the last sequence number seen, and resolves once `signal` aborts (or on an authorization error).
 * Opening the stream is itself one audit entry, hence the backoff.
 */
export async function streamAudit(userId: string, options: StreamOptions): Promise<void> {
  const { signal } = options;
  const minBackoff = options.minBackoffMs ?? 1_000;
  const maxBackoff = options.maxBackoffMs ?? 30_000;
  let since = options.since ?? 0;
  let backoff = minBackoff;

  while (!signal.aborted) {
    options.onStatus?.("connecting");
    let openedAt: number | null = null;
    try {
      let res: Response;
      try {
        res = await fetch(buildUrl("/audit/stream", { since, include_reads: options.includeReads ?? false }), {
          headers: { Accept: "text/event-stream", "X-User-Id": userId },
          cache: "no-store",
          signal,
        });
      } catch (err: unknown) {
        if (signal.aborted) return;
        throw err instanceof ApiError ? err : new ApiError(0, `Cannot reach the API at ${API_BASE}`);
      }
      if (!res.ok) throw new ApiError(res.status, await readDetail(res));
      if (!res.body) throw new ApiError(0, "This browser cannot read streamed responses");
      openedAt = Date.now();
      options.onStatus?.("open");
      await readEventStream(
        res.body,
        (ev) => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(ev.data);
          } catch {
            return; // a malformed event is skipped, the stream continues
          }
          if (ev.event === "hello" && isStreamHello(parsed)) {
            if (parsed.head && parsed.head.seq < since) {
              // The log behind the API was replaced (a fresh data directory): follow the new chain from its head.
              since = parsed.head.seq;
              throw new StreamRestart("audit log replaced");
            }
            options.onHello?.(parsed);
          } else if (ev.event === "entry" && isStreamEntry(parsed)) {
            if (parsed.seq > since) since = parsed.seq;
            options.onEntry(parsed);
          }
        },
        signal,
      );
      if (signal.aborted) return;
      throw new ApiError(0, "The audit stream closed");
    } catch (err: unknown) {
      if (signal.aborted) return;
      if (err instanceof StreamRestart) {
        backoff = minBackoff;
        continue;
      }
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        options.onError?.(describeError(err), null);
        options.onStatus?.("closed");
        return;
      }
      // A connection that stayed healthy for a while starts the backoff over.
      if (openedAt !== null && Date.now() - openedAt > 60_000) backoff = minBackoff;
      options.onError?.(describeError(err), backoff);
      options.onStatus?.("retrying");
      await sleep(backoff, signal);
      backoff = Math.min(maxBackoff, backoff * 2);
    }
  }
}

// ---------------------------------------------------------------------------
// Admin (admin role)
// ---------------------------------------------------------------------------
export function getAdminState(userId: string): Promise<AdminState> {
  return call<AdminState>("/admin/state", { userId });
}

export function getAdminIndex(userId: string): Promise<IndexItem[]> {
  return call<IndexItem[]>("/admin/index", { userId });
}

export function setSlackMembership(userId: string, channelId: string, change: MembershipChange): Promise<MembershipResult> {
  return call<MembershipResult>(`/admin/slack/channels/${encodeURIComponent(channelId)}/members`, { method: "POST", userId, body: change });
}

export function editConfluencePage(userId: string, pageId: string, edit: ContentEdit): Promise<PageEditResult> {
  return call<PageEditResult>(`/admin/confluence/pages/${encodeURIComponent(pageId)}/edit`, { method: "POST", userId, body: edit });
}

export function setConfluenceRestrictions(userId: string, pageId: string, change: RestrictionsChange): Promise<RestrictionsResult> {
  return call<RestrictionsResult>(`/admin/confluence/pages/${encodeURIComponent(pageId)}/restrictions`, { method: "POST", userId, body: change });
}

export function addJiraComment(userId: string, key: string, comment: CommentIn): Promise<ItemResult> {
  return call<ItemResult>(`/admin/jira/issues/${encodeURIComponent(key)}/comment`, { method: "POST", userId, body: comment });
}

export function setJiraSecurityLevel(userId: string, key: string, change: SecurityLevelChange): Promise<SecurityLevelResult> {
  return call<SecurityLevelResult>(`/admin/jira/issues/${encodeURIComponent(key)}/security-level`, { method: "POST", userId, body: change });
}

export function shareDriveFile(userId: string, fileId: string, change: ShareChange): Promise<ShareResult> {
  return call<ShareResult>(`/admin/gdrive/files/${encodeURIComponent(fileId)}/share`, { method: "POST", userId, body: change });
}

export function editDriveFile(userId: string, fileId: string, edit: ContentEdit): Promise<ItemResult> {
  return call<ItemResult>(`/admin/gdrive/files/${encodeURIComponent(fileId)}/edit`, { method: "POST", userId, body: edit });
}

export function setGroupMembership(userId: string, group: string, change: GroupChange): Promise<GroupResult> {
  return call<GroupResult>(`/admin/groups/${encodeURIComponent(group)}/members`, { method: "POST", userId, body: change });
}

export function deleteItem(userId: string, itemId: string, notify = true): Promise<DeleteResult> {
  // item ids contain colons and dots; the route accepts a path segment so only escape the unsafe characters.
  return call<DeleteResult>(`/admin/items/${encodeURI(itemId)}`, { method: "DELETE", userId, query: { notify } });
}

export function getSyncStatus(userId: string): Promise<SyncStatus> {
  return call<SyncStatus>("/admin/sync/status", { userId });
}

export function pauseSync(userId: string): Promise<SyncStatus> {
  return call<SyncStatus>("/admin/sync/pause", { method: "POST", userId });
}

export function resumeSync(userId: string): Promise<SyncStatus> {
  return call<SyncStatus>("/admin/sync/resume", { method: "POST", userId });
}

export function runSync(userId: string): Promise<SyncRunResult> {
  return call<SyncRunResult>("/admin/sync/run", { method: "POST", userId });
}

export function invalidateEntitlements(userId: string): Promise<EntitlementCacheStats> {
  return call<EntitlementCacheStats>("/admin/entitlements/invalidate", { method: "POST", userId });
}

export interface ResetResult {
  reset: boolean;
  audit_seq: number;
  sync: SyncStatus;
}

/** Back to the fixture: platforms reloaded, index rebuilt, caches cleared. The audit log is never touched. */
export function resetDemo(userId: string): Promise<ResetResult> {
  return call<ResetResult>("/admin/reset", { method: "POST", userId });
}
