import { describe, expect, it } from "vitest";

import type { CellValue, ResponseTable } from "../api/ApiClient";
import { ValueFormatter } from "../format/ValueFormatter";
import { GridColumns } from "./GridColumns";

const TABLE: ResponseTable = {
  handle: "r2",
  title: "Sprzedaż",
  columns: [
    { name: "net.sales", type: "decimal(19,4)" },
    { name: "order count", type: "int" },
    { name: "City", type: "string" },
    { name: "month", type: "date" },
  ],
  rows: [["1234.5000", 7, "Berlin", "2024-01-01"]],
  row_count: 1,
  truncated: false,
};

const columns = new GridColumns(new ValueFormatter());

describe("GridColumns", () => {
  it("keys rows by column position, so names with dots or spaces still show", () => {
    const definitions = columns.definitions(TABLE.columns);
    const [row] = columns.rows(TABLE);

    expect(definitions.map((definition) => definition.headerName)).toEqual([
      "net.sales",
      "order count",
      "City",
      "month",
    ]);
    expect(definitions.map((definition) => row?.[definition.field ?? ""])).toEqual([
      "1234.5000",
      7,
      "Berlin",
      "2024-01-01",
    ]);
  });

  it("gives numeric columns a number filter and right alignment", () => {
    const [decimal, integer, text, date] = columns.definitions(TABLE.columns);

    expect([decimal?.filter, integer?.filter]).toEqual([
      "agNumberColumnFilter",
      "agNumberColumnFilter",
    ]);
    expect([decimal?.type, integer?.type]).toEqual(["rightAligned", "rightAligned"]);
    expect([text?.filter, date?.filter]).toEqual(["agTextColumnFilter", "agTextColumnFilter"]);
    expect(text?.type).toBeUndefined();
  });

  it("sorts a decimal column by value", () => {
    const [decimal] = columns.definitions(TABLE.columns);
    const values = ["10.00", "9.50", "-5.00"];

    // The grid calls the comparator with the two cell values first; the rest is unused here.
    const compare = decimal?.comparator as (left: CellValue, right: CellValue) => number;

    const sorted = [...values].sort(compare);

    expect(sorted).toEqual(["-5.00", "9.50", "10.00"]);
  });
});
