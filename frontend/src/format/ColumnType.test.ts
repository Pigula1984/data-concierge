import { describe, expect, it } from "vitest";

import { ColumnType } from "./ColumnType";

describe("ColumnType.parse", () => {
  it("reads the kind of a decimal with precision and scale", () => {
    const type = ColumnType.parse("decimal(38,6)");

    expect(type.kind).toBe("decimal");
    expect(type.isNumber).toBe(true);
    expect(type.scale).toBe(6);
  });

  it("gives other kinds a scale of 0", () => {
    expect(ColumnType.parse("int").scale).toBe(0);
  });

  it.each(["int", "string", "date", "datetime", "bool"] as const)("reads the kind %s", (kind) => {
    expect(ColumnType.parse(kind).kind).toBe(kind);
  });

  it("treats an unknown type as text", () => {
    const type = ColumnType.parse("varchar");

    expect(type.kind).toBe("string");
    expect(type.isNumber).toBe(false);
  });
});
