import type { ChartKind, ResponseChart, ResponseTable } from "../api/ApiClient";
import { ColumnType, type ColumnKind } from "../format/ColumnType";

const ORDER: readonly ChartKind[] = ["line", "bar", "stacked_bar", "pie", "scatter"];

const LABELS: Record<ChartKind, string> = {
  line: "Liniowy",
  bar: "Słupkowy",
  stacked_bar: "Słupkowy skumulowany",
  pie: "Kołowy",
  scatter: "Punktowy",
};

// The same limits as ChartPolicy in the backend (plan 3B, KTD3); the switcher only redraws on
// the page, so a difference would never reach the model.
const MAX_LINE_POINTS = 1000;
const MAX_CATEGORIES = 50;
const MAX_PIE_SLICES = 8;
const MAX_SERIES = 12;

/** The chart kinds the page may switch a chart to, given the rows of its table. */
export class ChartKinds {
  static label(kind: ChartKind): string {
    return LABELS[kind];
  }

  compatible(chart: ResponseChart, table: ResponseTable): ChartKind[] {
    if (table.truncated || chart.y.length === 0) {
      return [];
    }
    const kinds = new Map(
      table.columns.map((column) => [column.name, ColumnType.parse(column.type)]),
    );
    const x = kinds.get(chart.x);
    const yNumeric = chart.y.every((name) => kinds.get(name)?.isNumber === true);
    if (x === undefined || !yNumeric) {
      return [];
    }
    const seriesCount = this.seriesCount(chart, table);
    if (seriesCount > MAX_SERIES) {
      return [];
    }
    return ORDER.filter((kind) => this.allows(kind, chart, table, x.kind));
  }

  private allows(
    kind: ChartKind,
    chart: ResponseChart,
    table: ResponseTable,
    x: ColumnKind,
  ): boolean {
    const rows = table.rows.length;
    const singleY = chart.y.length === 1;
    const hasSeries = chart.series != null;
    switch (kind) {
      case "line":
        return x !== "bool" && rows <= MAX_LINE_POINTS;
      case "bar":
        return this.distinct(table, chart.x) <= MAX_CATEGORIES;
      case "stacked_bar":
        return (hasSeries || !singleY) && this.distinct(table, chart.x) <= MAX_CATEGORIES;
      case "pie":
        return x === "string" && singleY && !hasSeries && rows <= MAX_PIE_SLICES;
      case "scatter":
        return (x === "int" || x === "decimal") && singleY && rows <= MAX_LINE_POINTS;
    }
  }

  private seriesCount(chart: ResponseChart, table: ResponseTable): number {
    return chart.series == null ? chart.y.length : this.distinct(table, chart.series);
  }

  private distinct(table: ResponseTable, column: string): number {
    const index = table.columns.findIndex((candidate) => candidate.name === column);
    return new Set(table.rows.map((row) => row[index] ?? null)).size;
  }
}
