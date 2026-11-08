import { describe, expect, it } from "vitest";

import type { CellValue, ResponseTable } from "../api/ApiClient";
import { ValueFormatter } from "../format/ValueFormatter";
import { GridColumns, type ColumnWidths, type GridRow } from "./GridColumns";

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
    // Only the cells and the label move right; the header icons stay on the right edge like in
    // every other column, so they are not read as part of the column to the left.
    expect([decimal?.cellClass, integer?.cellClass]).toEqual([
      "ag-right-aligned-cell",
      "ag-right-aligned-cell",
    ]);
    expect([decimal?.headerClass, integer?.headerClass]).toEqual([
      "result-grid__number-header",
      "result-grid__number-header",
    ]);
    expect([text?.filter, date?.filter]).toEqual(["agTextColumnFilter", "agTextColumnFilter"]);
    expect([text?.cellClass, text?.headerClass, text?.type]).toEqual([
      undefined,
      undefined,
      undefined,
    ]);
  });

  it("filters numeric columns by number, since decimals arrive as text", () => {
    const [decimal, , text] = columns.definitions(TABLE.columns);
    // The grid passes the row in `data`; the rest of the parameters are unused here.
    const filterValue = (row: GridRow) =>
      (decimal?.filterValueGetter as (params: { data: GridRow }) => unknown)({ data: row });

    expect(filterValue({ c0: "1234.5000" })).toBe(1234.5);
    expect(filterValue({ c0: null })).toBeNull();
    expect(text?.filterValueGetter).toBeUndefined();
  });

  it("sorts a decimal column by value", () => {
    const [decimal] = columns.definitions(TABLE.columns);
    const values = ["10.00", "9.50", "-5.00"];

    // The grid calls the comparator with the two cell values first; the rest is unused here.
    const compare = decimal?.comparator as (left: CellValue, right: CellValue) => number;

    const sorted = [...values].sort(compare);

    expect(sorted).toEqual(["-5.00", "9.50", "10.00"]);
  });

  it.each([
    [[120, 150], 1000, true],
    [[400, 500, 300], 1000, false],
  ])(
    "stretches columns %j sized to their values only into free space (%i px)",
    (widths, available, stretched) => {
      const calls: string[] = [];
      // Only the two calls the method makes; the grid's Column has many more members.
      const api = {
        getColumns: () => widths.map((width) => ({ getActualWidth: () => width })),
        sizeColumnsToFit: () => calls.push("fit"),
      } as unknown as ColumnWidths;

      columns.fillWidth(api, available);

      expect(calls).toEqual(stretched ? ["fit"] : []);
    },
  );
});
