import type { ColDef } from "ag-grid-community";

import type { CellValue, ResponseColumn, ResponseTable } from "../api/ApiClient";
import { ColumnType } from "../format/ColumnType";
import type { ValueFormatter } from "../format/ValueFormatter";

export type GridRow = Record<string, CellValue>;

/**
 * Turns a response table into grid columns and rows. Rows are keyed by column position, not
 * by name, because a column name may hold dots or spaces the grid would read as a path.
 */
export class GridColumns {
  private readonly formatter: ValueFormatter;

  constructor(formatter: ValueFormatter) {
    this.formatter = formatter;
  }

  definitions(columns: readonly ResponseColumn[]): ColDef<GridRow>[] {
    return columns.map((column, index) => this.definition(column, index));
  }

  rows(table: ResponseTable): GridRow[] {
    return table.rows.map((row) =>
      Object.fromEntries(row.map((value, index) => [GridColumns.field(index), value])),
    );
  }

  private definition(column: ResponseColumn, index: number): ColDef<GridRow> {
    const type = ColumnType.parse(column.type);
    const field = GridColumns.field(index);
    return {
      headerName: column.name,
      field,
      valueFormatter: (params) => this.formatter.format(params.value as CellValue, type),
      comparator: (left: CellValue, right: CellValue) => this.formatter.compare(left, right, type),
      filter: type.isNumber ? "agNumberColumnFilter" : "agTextColumnFilter",
      // Decimals arrive as text; the number filter compares numbers.
      filterValueGetter: type.isNumber
        ? (params) => GridColumns.number(params.data?.[field] ?? null)
        : undefined,
      type: type.isNumber ? "rightAligned" : undefined,
    };
  }

  private static field(index: number): string {
    return `c${String(index)}`;
  }

  private static number(value: CellValue): number | null {
    return value === null ? null : Number(value);
  }
}
