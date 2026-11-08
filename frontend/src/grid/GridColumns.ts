import type { ColDef, GridApi } from "ag-grid-community";

import type { CellValue, ResponseColumn, ResponseTable } from "../api/ApiClient";
import { ColumnType } from "../format/ColumnType";
import type { ValueFormatter } from "../format/ValueFormatter";

export type GridRow = Record<string, CellValue>;

/** The part of the grid's API that sizes columns. */
export type ColumnWidths = Pick<GridApi<GridRow>, "getColumns" | "sizeColumnsToFit">;

/**
 * Turns a response table into grid columns and rows. Rows are keyed by column position, not
 * by name, because a column name may hold dots or spaces the grid would read as a path.
 */
export class GridColumns {
  private readonly formatter: ValueFormatter;

  constructor(formatter: ValueFormatter) {
    this.formatter = formatter;
  }

  definitions(columns: readonly ResponseColumn[]): ColDef<GridRow, CellValue>[] {
    return columns.map((column, index) => this.definition(column, index));
  }

  /**
   * Called once the columns are as wide as their values: a table narrower than the space it
   * has is stretched to fill it; a wider one keeps its widths and scrolls sideways.
   */
  fillWidth(api: ColumnWidths, available: number): void {
    const used = (api.getColumns() ?? []).reduce(
      (width, column) => width + column.getActualWidth(),
      0,
    );
    if (used < available) {
      api.sizeColumnsToFit();
    }
  }

  rows(table: ResponseTable): GridRow[] {
    return table.rows.map((row) =>
      Object.fromEntries(row.map((value, index) => [GridColumns.field(index), value])),
    );
  }

  private definition(column: ResponseColumn, index: number): ColDef<GridRow, CellValue> {
    const type = ColumnType.parse(column.type);
    const field = GridColumns.field(index);
    return {
      headerName: column.name,
      field,
      valueFormatter: (params) => this.formatter.format(params.value ?? null, type),
      comparator: (left, right) => this.formatter.compare(left ?? null, right ?? null, type),
      filter: type.isNumber ? "agNumberColumnFilter" : "agTextColumnFilter",
      // Decimals arrive as text; the number filter compares numbers.
      filterValueGetter: type.isNumber
        ? (params) => GridColumns.number(params.data?.[field] ?? null)
        : undefined,
      // Not the grid's "rightAligned" type: it also moves the header icons to the left edge,
      // where they read as part of the previous column.
      cellClass: type.isNumber ? "ag-right-aligned-cell" : undefined,
      headerClass: type.isNumber ? "result-grid__number-header" : undefined,
    };
  }

  private static field(index: number): string {
    return `c${String(index)}`;
  }

  private static number(value: CellValue): number | null {
    return value === null ? null : Number(value);
  }
}
