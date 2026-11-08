import type { CellValue, ResponseChart, ResponseColumn, ResponseTable } from "../api/ApiClient";
import type { ChartOption } from "./ChartOptionsBuilder";
import type { ChartHost, ChartSurface } from "./EChartsHost";

const MONTH_COLUMNS: ResponseColumn[] = [
  { name: "month", type: "date" },
  { name: "net_sales", type: "decimal(38,2)" },
  { name: "order_count", type: "int" },
];

/** Twelve months of 2024 with sales and an order count, like the monthly report. */
export function months(overrides: Partial<ResponseTable> = {}): ResponseTable {
  const rows: CellValue[][] = Array.from({ length: 12 }, (_, index) => [
    `2024-${String(index + 1).padStart(2, "0")}-01`,
    `${String(2000000 + index * 1000)}.50`,
    300 + index,
  ]);
  return table(MONTH_COLUMNS, rows, overrides);
}

/** Sales of five product categories. */
export function categories(
  names: string[] = ["Bikes", "Components", "Clothing", "Accessories", "Other"],
): ResponseTable {
  return table(
    [
      { name: "category", type: "string" },
      { name: "net_sales", type: "decimal(38,2)" },
    ],
    names.map((name, index) => [name, `${String(1000 * (index + 1))}.25`]),
  );
}

export function table(
  columns: ResponseColumn[],
  rows: CellValue[][],
  overrides: Partial<ResponseTable> = {},
): ResponseTable {
  return {
    handle: "r2",
    title: "Tabela",
    columns,
    rows,
    row_count: rows.length,
    truncated: false,
    ...overrides,
  };
}

export function chart(overrides: Partial<ResponseChart> = {}): ResponseChart {
  return {
    handle: "r2",
    kind: "line",
    x: "month",
    y: ["net_sales"],
    series: null,
    title: "Sprzedaż netto po miesiącach",
    ...overrides,
  };
}

/** A drawn chart that records the options it was given. */
export class FakeSurface implements ChartSurface {
  readonly options: ChartOption[] = [];
  disposed = false;

  setOption(option: ChartOption): void {
    this.options.push(option);
  }

  resize(): void {
    // Nothing to lay out without a canvas.
  }

  dispose(): void {
    this.disposed = true;
  }
}

/** Creates fake charts, since jsdom has no canvas for ECharts. */
export class FakeHost implements ChartHost {
  readonly surfaces: FakeSurface[] = [];

  create(): ChartSurface {
    const surface = new FakeSurface();
    this.surfaces.push(surface);
    return surface;
  }

  /** The series types of the latest options of the latest chart. */
  lastSeriesTypes(): string[] {
    const option = this.surfaces.at(-1)?.options.at(-1);
    // The option type is ECharts' wide union; the fake reads only each series' type.
    const series = (option?.series ?? []) as { type: string }[];
    return series.map((one) => one.type);
  }
}
