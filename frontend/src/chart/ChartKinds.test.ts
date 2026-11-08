import { describe, expect, it } from "vitest";

import { ChartKinds } from "./ChartKinds";
import { categories, chart, months, table } from "./testing";

const kinds = new ChartKinds();

describe("ChartKinds", () => {
  it("offers line and bar for months and one measure, but no pie", () => {
    expect(kinds.compatible(chart(), months())).toEqual(["line", "bar"]);
  });

  it("offers a pie for a few categories", () => {
    expect(kinds.compatible(chart({ x: "category", kind: "bar" }), categories())).toEqual([
      "line",
      "bar",
      "pie",
    ]);
  });

  it("offers no pie for nine categories", () => {
    const nine = categories(["a", "b", "c", "d", "e", "f", "g", "h", "i"]);

    expect(kinds.compatible(chart({ x: "category" }), nine)).not.toContain("pie");
  });

  it("offers a stacked bar only with a series or several measures", () => {
    const two = chart({ y: ["net_sales", "order_count"] });

    expect(kinds.compatible(two, months())).toContain("stacked_bar");
    expect(kinds.compatible(chart(), months())).not.toContain("stacked_bar");
  });

  it("offers a scatter for a numeric x", () => {
    expect(kinds.compatible(chart({ x: "order_count" }), months())).toContain("scatter");
  });

  it("offers nothing for a truncated table", () => {
    expect(kinds.compatible(chart(), months({ truncated: true }))).toEqual([]);
  });

  it("offers nothing when a measure is text or more than 12 series would be drawn", () => {
    const long = table(
      [
        { name: "month", type: "date" },
        { name: "category", type: "string" },
        { name: "net_sales", type: "decimal(38,2)" },
      ],
      Array.from({ length: 13 }, (_, index) => ["2024-01-01", `c${String(index)}`, "1.00"]),
    );

    expect(kinds.compatible(chart({ y: ["category"] }), long)).toEqual([]);
    expect(kinds.compatible(chart({ series: "category" }), long)).toEqual([]);
  });

  it("names each kind in Polish", () => {
    expect(ChartKinds.label("stacked_bar")).toBe("Słupkowy skumulowany");
  });
});
