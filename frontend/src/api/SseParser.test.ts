import { describe, expect, it } from "vitest";

import { SseParser } from "./SseParser";

describe("SseParser", () => {
  it("gives each event once its blank line arrives", () => {
    const parser = new SseParser();

    expect(parser.push('data: {"step":"guard"}\n')).toEqual([]);
    expect(parser.push("\n")).toEqual([{ event: "message", data: '{"step":"guard"}' }]);
  });

  it("joins an event split in the middle of a line", () => {
    const parser = new SseParser();

    const first = parser.push('data: {"st');
    const second = parser.push('ep":"model"}\n\ndata: 2\n\n');

    expect(first).toEqual([]);
    expect(second).toEqual([
      { event: "message", data: '{"step":"model"}' },
      { event: "message", data: "2" },
    ]);
  });

  it("reads CRLF line breaks, also when a chunk ends between CR and LF", () => {
    const parser = new SseParser();

    const first = parser.push("data: a\r");
    const second = parser.push("\n\r\n");

    expect(first).toEqual([]);
    expect(second).toEqual([{ event: "message", data: "a" }]);
  });

  it("joins several data lines with a line break and keeps the event name", () => {
    const parser = new SseParser();

    expect(parser.push("event: progress\ndata: one\ndata: two\n\n")).toEqual([
      { event: "progress", data: "one\ntwo" },
    ]);
  });

  it("skips keepalive comments", () => {
    const parser = new SseParser();

    expect(parser.push(": ping\n\ndata: x\n\n")).toEqual([{ event: "message", data: "x" }]);
  });
});
