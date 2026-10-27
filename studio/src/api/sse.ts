// Minimal text/event-stream parser. We read SSE with fetch (not EventSource)
// so the in-memory bearer token from a launch session can be sent as a header.

export type RawEvent = { event: string; data: string };

export class SseParser {
  private buffer = "";

  /** Feed a decoded chunk; returns the events completed by it. */
  push(chunk: string): RawEvent[] {
    this.buffer += chunk.replace(/\r\n?/g, "\n");
    const out: RawEvent[] = [];
    let idx: number;
    while ((idx = this.buffer.indexOf("\n\n")) !== -1) {
      const block = this.buffer.slice(0, idx);
      this.buffer = this.buffer.slice(idx + 2);
      let event = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (!line || line.startsWith(":")) continue;
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        let value = colon === -1 ? "" : line.slice(colon + 1);
        if (value.startsWith(" ")) value = value.slice(1);
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
      if (data.length) out.push({ event, data: data.join("\n") });
    }
    return out;
  }
}
