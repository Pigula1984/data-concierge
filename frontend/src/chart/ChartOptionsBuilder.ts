import type {
  BarSeriesOption,
  LineSeriesOption,
  PieSeriesOption,
  ScatterSeriesOption,
} from "echarts/charts";
import type {
  GridComponentOption,
  LegendComponentOption,
  ToolboxComponentOption,
  TooltipComponentOption,
} from "echarts/components";
import type { ComposeOption } from "echarts/core";

import type { CellValue, ChartKind, ResponseChart, ResponseTable } from "../api/ApiClient";
import { ColumnType } from "../format/ColumnType";
import type { ValueFormatter } from "../format/ValueFormatter";

type SeriesOption = LineSeriesOption | BarSeriesOption | PieSeriesOption | ScatterSeriesOption;

export type ChartOption = ComposeOption<
  | SeriesOption
  | GridComponentOption
  | LegendComponentOption
  | ToolboxComponentOption
  | TooltipComponentOption
>;

interface Series {
  name: string;
  values: (number | null)[];
}

// Decimals of a measure: "decimal(38,2)" draws its values with two decimal places.
const DECIMAL_SCALE = /^decimal\(\s*\d+\s*,\s*(\d+)\s*\)$/;

/**
 * Turns a chart of the response and the rows of its table into ECharts options. Values from
 * the data reach ECharts only as data and names: the tooltip keeps its default markup, which
 * escapes them, and gets only a `valueFormatter` for numbers (plan 3B, KTD6). Axis labels are
 * drawn on the canvas, so their formatters return plain text.
 */
export class ChartOptionsBuilder {
  private readonly formatter: ValueFormatter;

  constructor(formatter: ValueFormatter) {
    this.formatter = formatter;
  }

  build(chart: ResponseChart, table: ResponseTable, kind: ChartKind): ChartOption {
    const common: ChartOption = {
      animation: false,
      toolbox: {
        right: 8,
        feature: {
          saveAsImage: {
            title: "Zapisz jako PNG",
            name: this.fileName(chart.title),
            pixelRatio: 2,
          },
        },
      },
    };
    const series = this.series(chart, table);
    switch (kind) {
      case "pie":
        return { ...common, ...this.pie(chart, table, series) };
      case "scatter":
        return { ...common, ...this.scatter(chart, table, series) };
      default:
        return { ...common, ...this.categorical(chart, table, series, kind) };
    }
  }

  private categorical(
    chart: ResponseChart,
    table: ResponseTable,
    series: Series[],
    kind: "line" | "bar" | "stacked_bar",
  ): ChartOption {
    const categories = this.categories(chart, table);
    return {
      legend: { show: series.length > 1, top: 0 },
      grid: { left: 8, right: 16, top: series.length > 1 ? 40 : 24, bottom: 8, containLabel: true },
      tooltip: { trigger: "axis", valueFormatter: this.valueText(chart, table) },
      xAxis: {
        type: "category",
        data: categories.map((value) => this.text(chart.x, table, value)),
      },
      yAxis: { type: "value", axisLabel: { formatter: (value: number) => this.axisText(value) } },
      series: series.map((one): LineSeriesOption | BarSeriesOption =>
        kind === "line"
          ? { type: "line", name: one.name, data: one.values }
          : {
              type: "bar",
              name: one.name,
              data: one.values,
              ...(kind === "stacked_bar" ? { stack: "total" } : {}),
            },
      ),
    };
  }

  private pie(chart: ResponseChart, table: ResponseTable, series: Series[]): ChartOption {
    const categories = this.categories(chart, table);
    const values = series[0]?.values ?? [];
    return {
      legend: { show: true, type: "scroll", bottom: 0 },
      tooltip: { trigger: "item", valueFormatter: this.valueText(chart, table) },
      series: [
        {
          type: "pie",
          name: series[0]?.name ?? "",
          radius: ["0%", "65%"],
          data: categories.map((value, index) => ({
            name: this.text(chart.x, table, value),
            value: values[index] ?? 0,
          })),
        },
      ],
    };
  }

  private scatter(chart: ResponseChart, table: ResponseTable, series: Series[]): ChartOption {
    const x = this.columnIndex(table, chart.x);
    const y = this.columnIndex(table, chart.y[0] ?? "");
    const points = this.groups(chart, table).map(([group, rows]): ScatterSeriesOption => ({
      type: "scatter",
      name: chart.series == null ? (series[0]?.name ?? "") : this.text(chart.series, table, group),
      data: rows.map((row) => [this.number(row[x] ?? null), this.number(row[y] ?? null)]),
    }));
    return {
      legend: { show: points.length > 1, top: 0 },
      grid: { left: 8, right: 16, top: points.length > 1 ? 40 : 24, bottom: 8, containLabel: true },
      tooltip: { trigger: "item", valueFormatter: this.valueText(chart, table) },
      xAxis: {
        type: "value",
        name: chart.x,
        axisLabel: { formatter: (value: number) => this.axisText(value) },
      },
      yAxis: { type: "value", axisLabel: { formatter: (value: number) => this.axisText(value) } },
      series: points,
    };
  }

  /** One series per y column, or per value of the series column (long format). */
  private series(chart: ResponseChart, table: ResponseTable): Series[] {
    const categories = this.categories(chart, table);
    const x = this.columnIndex(table, chart.x);
    if (chart.series == null) {
      return chart.y.map((name) => {
        const y = this.columnIndex(table, name);
        const byX = new Map(table.rows.map((row) => [row[x] ?? null, this.number(row[y] ?? null)]));
        return { name, values: categories.map((value) => byX.get(value) ?? null) };
      });
    }
    const y = this.columnIndex(table, chart.y[0] ?? "");
    return this.groups(chart, table).map(([group, rows]) => {
      const byX = new Map(rows.map((row) => [row[x] ?? null, this.number(row[y] ?? null)]));
      return {
        name: this.text(chart.series ?? "", table, group),
        values: categories.map((value) => byX.get(value) ?? null),
      };
    });
  }

  /** Rows by the value of the series column, in table order; one group without a series. */
  private groups(chart: ResponseChart, table: ResponseTable): [CellValue, CellValue[][]][] {
    if (chart.series == null) {
      return [[null, table.rows]];
    }
    const index = this.columnIndex(table, chart.series);
    const groups = new Map<CellValue, CellValue[][]>();
    for (const row of table.rows) {
      const key = row[index] ?? null;
      groups.set(key, [...(groups.get(key) ?? []), row]);
    }
    return [...groups.entries()];
  }

  /** Distinct x values in table order. */
  private categories(chart: ResponseChart, table: ResponseTable): CellValue[] {
    const index = this.columnIndex(table, chart.x);
    return [...new Set(table.rows.map((row) => row[index] ?? null))];
  }

  private text(column: string, table: ResponseTable, value: CellValue): string {
    const type = table.columns.find((candidate) => candidate.name === column)?.type ?? "string";
    return this.formatter.format(value, ColumnType.parse(type)) || "brak danych";
  }

  /** Tooltip numbers in Polish, at the largest scale of the drawn columns: "12 345,50". */
  private valueText(chart: ResponseChart, table: ResponseTable): (value: unknown) => string {
    const scale = Math.max(
      0,
      ...chart.y.map((name) => {
        const type = table.columns.find((column) => column.name === name)?.type ?? "";
        return Number(DECIMAL_SCALE.exec(type)?.[1] ?? 0);
      }),
    );
    const format = new Intl.NumberFormat("pl-PL", {
      minimumFractionDigits: scale,
      maximumFractionDigits: scale,
    });
    return (value) => (typeof value === "number" ? format.format(value) : "brak danych");
  }

  private axisText(value: number): string {
    return new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 2 }).format(value);
  }

  private number(value: CellValue): number | null {
    return value === null || typeof value === "boolean" ? null : Number(value);
  }

  private columnIndex(table: ResponseTable, name: string): number {
    return table.columns.findIndex((column) => column.name === name);
  }

  private fileName(title: string): string {
    const slug = title
      .normalize("NFD")
      .replace(/[̀-ͯ]/g, "")
      .replace(/ł/g, "l")
      .replace(/Ł/g, "L")
      .replace(/[^A-Za-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .toLowerCase();
    return slug || "wykres";
  }
}
