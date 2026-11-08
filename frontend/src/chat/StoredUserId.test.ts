import { describe, expect, it } from "vitest";

import { StoredUserId } from "./StoredUserId";

function blocked(): Storage {
  throw new DOMException("The operation is insecure.", "SecurityError");
}

describe("StoredUserId", () => {
  it("remembers the saved id", () => {
    const store = new StoredUserId(() => window.localStorage);

    store.save("7");

    expect(new StoredUserId(() => window.localStorage).load()).toBe("7");
  });

  it("falls back to user 1 when storage is blocked", () => {
    const store = new StoredUserId(blocked);

    expect(() => {
      store.save("7");
    }).not.toThrow();
    expect(store.load()).toBe("1");
  });

  it.each([
    ["7", 7],
    [" 12 ", 12],
    ["", null],
    ["abc", null],
    ["1.5", null],
    ["-3", null],
  ])("parses %j as %j", (text, expected) => {
    expect(StoredUserId.parse(text)).toBe(expected);
  });
});
