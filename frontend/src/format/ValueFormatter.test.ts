import { describe, expect, it } from "vitest";

import { ColumnType } from "./ColumnType";
import { ValueFormatter } from "./ValueFormatter";

const NBSP = "\u00a0";
const formatter = new ValueFormatter();

// The same rules as `data-concierge chat` (ValueFormatter and ConversationPrinter in Python).
describe("ValueFormatter.format", () => {
  it.each([
    ["decimal(38,4)", "32145678.1200", `32${NBSP}145${NBSP}678,1200`],
    ["decimal(19,4)", "1234.5000", "1234,5000"],
    ["decimal(19,4)", "12345.0000", `12${NBSP}345,0000`],
    ["decimal(19,2)", "-12345.50", `-12${NBSP}345,50`],
    ["decimal(10,0)", "1234567", `1${NBSP}234${NBSP}567`],
    ["int", 14244, "14244"],
    ["date", "2024-06-01", "2024-06-01"],
    ["datetime", "2024-06-01T10:15:00", "2024-06-01T10:15:00"],
    ["string", "Berlin", "Berlin"],
    ["bool", true, "tak"],
    ["bool", false, "nie"],
  ])("formats a %s value %j as %j", (type, value, expected) => {
    expect(formatter.format(value, ColumnType.parse(type))).toBe(expected);
  });

  it("leaves an empty cell for a missing value", () => {
    expect(formatter.format(null, ColumnType.parse("decimal(19,4)"))).toBe("");
  });
});

describe("ValueFormatter.count", () => {
  it.each([
    [3, "3"],
    [1755, "1755"],
    [10000, `10${NBSP}000`],
    [14244, `14${NBSP}244`],
  ])("groups a row count %j in a sentence as %j", (value, expected) => {
    expect(formatter.count(value)).toBe(expected);
  });
});

describe("ValueFormatter.compare", () => {
  it("sorts decimals by value, not as text", () => {
    const type = ColumnType.parse("decimal(19,2)");
    const values = ["10.00", "9.50", "-5.00"];

    const sorted = [...values].sort((a, b) => formatter.compare(a, b, type));

    expect(sorted).toEqual(["-5.00", "9.50", "10.00"]);
  });

  it("puts missing values before numbers", () => {
    const type = ColumnType.parse("int");
    const values = [3, null, 1];

    const sorted = [...values].sort((a, b) => formatter.compare(a, b, type));

    expect(sorted).toEqual([null, 1, 3]);
  });

  it("sorts text in Polish order", () => {
    const type = ColumnType.parse("string");
    const values = ["Żory", "Łódź", "Zabrze", "Lublin"];

    const sorted = [...values].sort((a, b) => formatter.compare(a, b, type));

    expect(sorted).toEqual(["Lublin", "Łódź", "Zabrze", "Żory"]);
  });
});
