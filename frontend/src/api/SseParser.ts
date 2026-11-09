/** One server-sent event: its name (`message` when the stream gives none) and its data. */
export interface SseEvent {
  event: string;
  data: string;
}

const LINE_BREAK = /\r\n|\r|\n/;

/**
 * Splits a `text/event-stream` body into events as its chunks arrive. A chunk may end in the
 * middle of a line or an event, so the unfinished rest waits for the next chunk. Comments, such
 * as the server's keepalive pings, and events without data are skipped.
 */
export class SseParser {
  private buffer = "";
  private data: string[] = [];
  private name = "message";

  push(chunk: string): SseEvent[] {
    let text = this.buffer + chunk;
    // A "\r" at the end may be the first half of "\r\n", so it waits for the next chunk.
    const held = text.endsWith("\r") ? "\r" : "";
    text = text.slice(0, text.length - held.length);
    const lines = text.split(LINE_BREAK);
    this.buffer = (lines.pop() ?? "") + held;
    return lines.flatMap((line) => this.line(line));
  }

  private line(line: string): SseEvent[] {
    if (line === "") {
      const event = this.data.length > 0 ? [{ event: this.name, data: this.data.join("\n") }] : [];
      this.data = [];
      this.name = "message";
      return event;
    }
    if (!line.startsWith(":")) {
      this.field(line);
    }
    return [];
  }

  private field(line: string): void {
    const colon = line.indexOf(":");
    const name = colon === -1 ? line : line.slice(0, colon);
    const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
    if (name === "data") {
      this.data.push(value);
    } else if (name === "event") {
      this.name = value;
    }
  }
}
