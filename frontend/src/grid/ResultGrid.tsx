import { AllCommunityModule, themeQuartz, type ColDef } from "ag-grid-community";
import { AgGridReact } from "ag-grid-react";
import { useMemo } from "react";

import type { ResponseTable } from "../api/ApiClient";
import { ValueFormatter } from "../format/ValueFormatter";
import { GridColumns, type GridRow } from "./GridColumns";

const GRID_COLUMNS = new GridColumns(new ValueFormatter());
const GRID_MODULES = [AllCommunityModule];
const DEFAULT_COLUMN: ColDef<GridRow> = { sortable: true, resizable: true, minWidth: 110 };
// Up to this many rows the grid grows with its rows; longer tables scroll in a fixed height.
const FIT_ROWS = 15;

export function ResultGrid({ table }: { table: ResponseTable }) {
  const columnDefs = useMemo(() => GRID_COLUMNS.definitions(table.columns), [table.columns]);
  const rowData = useMemo(() => GRID_COLUMNS.rows(table), [table]);
  const fits = table.rows.length <= FIT_ROWS;

  return (
    <section className="result-table">
      <h3 className="result-title">{table.title}</h3>
      <TruncationNote table={table} />
      {table.rows.length === 0 ? (
        <p className="result-empty">Brak wierszy.</p>
      ) : (
        <div className={`result-grid ${fits ? "result-grid--fit" : "result-grid--scroll"}`}>
          <AgGridReact<GridRow>
            modules={GRID_MODULES}
            theme={themeQuartz}
            columnDefs={columnDefs}
            rowData={rowData}
            defaultColDef={DEFAULT_COLUMN}
            domLayout={fits ? "autoHeight" : "normal"}
          />
        </div>
      )}
    </section>
  );
}

function TruncationNote({ table }: { table: ResponseTable }) {
  if (!table.truncated) {
    return null;
  }
  const shown = table.rows.length;
  const text =
    shown < table.row_count
      ? `Pokazano ${String(shown)} z ${String(table.row_count)} wierszy (limit odpowiedzi).`
      : "Wynik niepełny: procedura źródłowa zwróciła tylko część danych (limit wierszy).";
  return (
    <p className="result-note" role="note">
      {text}
    </p>
  );
}
