import { describe, expect, it } from "vitest";

import { ValueFormatter } from "../format/ValueFormatter";
import { ChartOptionsBuilder, type ChartOption } from "./ChartOptionsBuilder";
import { categories, chart, months, table } from "./testing";

const builder = new ChartOptionsBuilder(new ValueFormatter());
const NBSP = " ";

interface DrawnSeries {
  type: string;
  name: string;
  data: unknown[];
  stack?: string;
}

// The builder returns ECharts' wide option type; the tests read the few fields they check.
function drawn(option: ChartOption): DrawnSeries[] {
  return option.series as DrawnSeries[];
}

function tooltipValue(option: ChartOption, value: number): string {
  const tooltip = option.tooltip as { valueFormatter: (value: unknown) => string };
  return tooltip.valueFormatter(value);
}

describe("ChartOptionsBuilder", () => {
  it("draws one line over the months, with numbers parsed from the decimal text", () => {
    const option = builder.build(chart(), months(), "line");

    const [line] = drawn(option);
    expect(drawn(option)).toHaveLength(1);
    expect(line?.type).toBe("line");
    expect(line?.name).toBe("net_sales");
    expect(line?.data).toHaveLength(12);
    expect(line?.data[0]).toBe(2000000.5);
    expect((option.xAxis as { data: string[] }).data[0]).toBe("2024-01-01");
    expect((option.legend as { show: boolean }).show).toBe(false);
  });

  it("draws one series per measure and shows the legend", () => {
    const option = builder.build(chart({ y: ["net_sales", "order_count"] }), months(), "bar");

    expect(drawn(option).map((one) => [one.type, one.name])).toEqual([
      ["bar", "net_sales"],
      ["bar", "order_count"],
    ]);
    expect((option.legend as { show: boolean }).show).toBe(true);
  });

  it("splits a long table into one series per value of the series column", () => {
    const long = table(
      [
        { name: "month", type: "date" },
        { name: "category", type: "string" },
        { name: "net_sales", type: "decimal(38,2)" },
      ],
      [
        ["2024-01-01", "Bikes", "10.00"],
        ["2024-01-01", "Clothing", "2.00"],
        ["2024-02-01", "Bikes", "12.00"],
      ],
    );

    const option = builder.build(chart({ series: "category" }), long, "stacked_bar");

    expect(drawn(option).map((one) => [one.name, one.data, one.stack])).toEqual([
      ["Bikes", [10, 12], "total"],
      ["Clothing", [2, null], "total"],
    ]);
  });

  it("draws a pie with the categories as slice names", () => {
    const option = builder.build(chart({ x: "category" }), categories(), "pie");

    const [pie] = drawn(option);
    expect(pie?.type).toBe("pie");
    expect(pie?.data[0]).toEqual({ name: "Bikes", value: 1000.25 });
  });

  it("draws a scatter on a numeric x axis", () => {
    const option = builder.build(chart({ x: "order_count" }), months(), "scatter");

    expect((option.xAxis as { type: string }).type).toBe("value");
    expect(drawn(option)[0]?.data[0]).toEqual([300, 2000000.5]);
  });

  it("formats tooltip values in Polish at the scale of the column, without its own markup", () => {
    const option = builder.build(chart(), months(), "line");

    expect(tooltipValue(option, 12345.5)).toBe(`12${NBSP}345,50`);
    expect(tooltipValue(option, 1234.5)).toBe("1234,50");
    // A formatter of its own would be inserted as HTML; the default markup escapes names.
    expect(option.tooltip).not.toHaveProperty("formatter");
  });

  it("offers a PNG download named after the title", () => {
    const option = builder.build(
      chart({ title: "Sprzedaż netto: łącznie 2024" }),
      months(),
      "line",
    );

    expect(option.toolbox).toEqual({
      right: 8,
      feature: {
        saveAsImage: {
          title: "Zapisz jako PNG",
          name: "sprzedaz-netto-lacznie-2024",
          pixelRatio: 2,
        },
      },
    });
  });

  it("keeps a category name with HTML as plain data", () => {
    const hostile = "<img src=x onerror=alert(1)>";

    const option = builder.build(chart({ x: "category" }), categories([hostile]), "bar");

    expect((option.xAxis as { data: string[] }).data).toEqual([hostile]);
  });
});
