/**
 * A small server-sent-events reader for fetch() responses.
 *
 * EventSource cannot send custom headers, and the audit stream needs X-User-Id, so the stream
 * is read with fetch + a ReadableStream reader and parsed here: lines end in LF, CRLF or CR;
 * a blank line dispatches the event; `event:` names it, `data:` lines are joined with "\n",
 * and lines starting with ":" (the server's keep-alives) are comments.
 */

export interface SseEvent {
  /** The event name; "message" when the server sent none. */
  event: string;
  data: string;
  id: string | null;
}

export class SseParser {
  private buffer = "";
  private eventName = "";
  private dataLines: string[] = [];
  private lastId: string | null = null;

  /** Feed decoded text; returns the events completed by it. */
  push(text: string): SseEvent[] {
    this.buffer += text;
    const events: SseEvent[] = [];
    for (;;) {
      const lf = this.buffer.indexOf("\n");
      const cr = this.buffer.indexOf("\r");
      let end: number;
      let skip: number;
      if (cr !== -1 && (lf === -1 || cr < lf)) {
        // A CR at the very end may be the first half of a CRLF split across chunks: wait for more.
        if (cr === this.buffer.length - 1) break;
        end = cr;
        skip = this.buffer[cr + 1] === "\n" ? 2 : 1;
      } else if (lf !== -1) {
        end = lf;
        skip = 1;
      } else {
        break;
      }
      const line = this.buffer.slice(0, end);
      this.buffer = this.buffer.slice(end + skip);
      const ev = this.line(line);
      if (ev) events.push(ev);
    }
    return events;
  }

  private line(line: string): SseEvent | null {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return null; // comment / keep-alive
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.eventName = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\0")) this.lastId = value;
        break;
      default:
        break; // "retry" and unknown fields are ignored
    }
    return null;
  }

  private dispatch(): SseEvent | null {
    const hadData = this.dataLines.length > 0;
    const ev: SseEvent = { event: this.eventName || "message", data: this.dataLines.join("\n"), id: this.lastId };
    this.eventName = "";
    this.dataLines = [];
    return hadData ? ev : null;
  }
}

/**
 * Read an event stream until it ends or `signal` aborts, calling `onEvent` for each complete event.
 * Resolves when the server closes the stream; rejects on a network error (unless aborted).
 */
export async function readEventStream(body: ReadableStream<Uint8Array>, onEvent: (ev: SseEvent) => void, signal?: AbortSignal): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SseParser();
  const cancel = () => {
    reader.cancel().catch(() => undefined);
  };
  signal?.addEventListener("abort", cancel, { once: true });
  try {
    for (;;) {
      let chunk: ReadableStreamReadResult<Uint8Array>;
      try {
        chunk = await reader.read();
      } catch (err: unknown) {
        if (signal?.aborted) return;
        throw err;
      }
      if (chunk.done) break;
      for (const ev of parser.push(decoder.decode(chunk.value, { stream: true }))) onEvent(ev);
      if (signal?.aborted) return;
    }
    // An event without its terminating blank line at end-of-stream is discarded, as the spec says.
    parser.push(decoder.decode());
  } catch (err: unknown) {
    // A handler gave up on this stream: release the connection before rethrowing.
    cancel();
    throw err;
  } finally {
    signal?.removeEventListener("abort", cancel);
  }
}
