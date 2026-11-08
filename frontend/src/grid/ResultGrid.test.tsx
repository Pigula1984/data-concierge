import { getDefaultNormalizer, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { CellValue, ResponseTable } from "../api/ApiClient";
import { ResultGrid } from "./ResultGrid";

const HOSTILE = "<img src=x onerror=alert(1)>";

function table(rows: CellValue[][], overrides: Partial<ResponseTable> = {}): ResponseTable {
  return {
    handle: "r2",
    title: "Klienci z Berlina",
    columns: [
      { name: "CustomerName", type: "string" },
      { name: "net_sales", type: "decimal(19,4)" },
    ],
    rows,
    row_count: rows.length,
    truncated: false,
    ...overrides,
  };
}

function rowsOf(count: number): CellValue[][] {
  return Array.from({ length: count }, (_, index) => [`Klient ${String(index)}`, "100.0000"]);
}

describe("ResultGrid", () => {
  it("shows a cell value with HTML in it as text, not as an element", async () => {
    const { container } = render(<ResultGrid table={table([[HOSTILE, "1.0000"]])} />);

    await waitFor(() => {
      expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    });
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows a column name with HTML in it as text, not as an element", async () => {
    // Column names are aliases the model writes in its query.
    const hostile = table([["Anna"]], { columns: [{ name: HOSTILE, type: "string" }] });
    const { container } = render(<ResultGrid table={hostile} />);

    await waitFor(() => {
      expect(screen.getByText(HOSTILE)).toBeInTheDocument();
    });
    expect(container.querySelector("img")).toBeNull();
  });

  it("shows the title and the formatted values", async () => {
    render(<ResultGrid table={table([["Anna", "12345.5000"]])} />);

    expect(screen.getByRole("heading", { name: "Klienci z Berlina" })).toBeInTheDocument();
    await waitFor(() => {
      // Keep the no-break space: the default normalizer would turn it into a plain space.
      const exact = { normalizer: getDefaultNormalizer({ collapseWhitespace: false }) };
      expect(screen.getByText("12\u00a0345,5000", exact)).toBeInTheDocument();
    });
  });

  it("says how many rows were shown when the response cut the table", () => {
    render(<ResultGrid table={table(rowsOf(3), { row_count: 14244, truncated: true })} />);

    expect(screen.getByRole("note").textContent).toBe(
      "Pokazano 3 z 14 244 wierszy (limit odpowiedzi).",
    );
  });

  it("says the result is incomplete when a source procedure was cut", () => {
    render(<ResultGrid table={table(rowsOf(3), { truncated: true })} />);

    expect(screen.getByRole("note")).toHaveTextContent("Wynik niepełny");
  });

  it("has no note for a complete table", () => {
    render(<ResultGrid table={table(rowsOf(3))} />);

    expect(screen.queryByRole("note")).toBeNull();
  });

  it("fits a small table to its rows", () => {
    const { container } = render(<ResultGrid table={table(rowsOf(15))} />);

    expect(container.querySelector(".result-grid")).toHaveClass("result-grid--fit");
  });

  it("gives a long table a fixed height with scrolling", () => {
    const { container } = render(<ResultGrid table={table(rowsOf(16))} />);

    expect(container.querySelector(".result-grid")).toHaveClass("result-grid--scroll");
  });

  it("says there are no rows instead of an empty grid", () => {
    const { container } = render(<ResultGrid table={table([])} />);

    expect(screen.getByText("Brak wierszy.")).toBeInTheDocument();
    expect(container.querySelector(".result-grid")).toBeNull();
  });
});
