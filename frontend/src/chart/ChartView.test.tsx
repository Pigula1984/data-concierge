import { render, screen } from "@testing-library/react";
import { StrictMode } from "react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import type { ResponseChart, ResponseTable } from "../api/ApiClient";
import { ChartView } from "./ChartView";
import { ChartHostContext } from "./EChartsHost";
import { FakeHost, chart, months } from "./testing";

function drawn(host: FakeHost, spec: ResponseChart = chart(), table: ResponseTable = months()) {
  return render(
    <ChartHostContext.Provider value={host}>
      <ChartView chart={spec} table={table} />
    </ChartHostContext.Provider>,
  );
}

describe("ChartView", () => {
  it("shows the title and draws the kind the model chose", () => {
    const host = new FakeHost();

    drawn(host);

    expect(
      screen.getByRole("heading", { name: "Sprzedaż netto po miesiącach" }),
    ).toBeInTheDocument();
    expect(host.lastSeriesTypes()).toEqual(["line"]);
  });

  it("redraws another kind on the page when the user switches it", async () => {
    const host = new FakeHost();
    drawn(host);

    await userEvent.selectOptions(screen.getByRole("combobox", { name: /Rodzaj wykresu/ }), "bar");

    expect(host.surfaces).toHaveLength(1);
    expect(host.lastSeriesTypes()).toEqual(["bar"]);
  });

  it("offers only the kinds the table allows", () => {
    drawn(new FakeHost());

    const offered = screen.getAllByRole("option").map((option) => option.textContent);
    expect(offered).toEqual(["Liniowy", "Słupkowy"]);
  });

  it("shows a title with HTML as text", () => {
    const hostile = "<img src=x onerror=alert(1)>";

    const { container } = drawn(new FakeHost(), chart({ title: hostile }));

    expect(screen.getByRole("heading", { name: hostile })).toBeInTheDocument();
    expect(container.querySelector("img")).toBeNull();
  });

  it("hides the kind switcher when the table allows no other kind", () => {
    drawn(new FakeHost(), chart(), months({ truncated: true }));

    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("draws on the chart that stays after StrictMode mounts the view twice", () => {
    const host = new FakeHost();

    render(
      <StrictMode>
        <ChartHostContext.Provider value={host}>
          <ChartView chart={chart()} table={months()} />
        </ChartHostContext.Provider>
      </StrictMode>,
    );

    expect(host.surfaces.length).toBeGreaterThan(1);
    expect(host.surfaces[0]?.disposed).toBe(true);
    expect(host.surfaces.at(-1)?.disposed).toBe(false);
    expect(host.surfaces.at(-1)?.options).not.toHaveLength(0);
  });

  it("releases the chart when the answer goes away", () => {
    const host = new FakeHost();
    const { unmount } = drawn(host);

    unmount();

    expect(host.surfaces[0]?.disposed).toBe(true);
  });
});
