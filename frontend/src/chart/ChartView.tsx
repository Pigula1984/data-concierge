import { useContext, useEffect, useMemo, useRef, useState } from "react";

import type { ChartKind, ResponseChart, ResponseTable } from "../api/ApiClient";
import { ValueFormatter } from "../format/ValueFormatter";
import { ChartKinds } from "./ChartKinds";
import { ChartOptionsBuilder } from "./ChartOptionsBuilder";
import { ChartHostContext, type ChartSurface } from "./EChartsHost";

const BUILDER = new ChartOptionsBuilder(new ValueFormatter());
const KINDS = new ChartKinds();

interface ChartViewProps {
  chart: ResponseChart;
  table: ResponseTable;
}

/** The chart of an answer, drawn from its table; the kind switches without the model. */
export function ChartView({ chart, table }: ChartViewProps) {
  const host = useContext(ChartHostContext);
  const element = useRef<HTMLDivElement>(null);
  const surface = useRef<ChartSurface | null>(null);
  const [kind, setKind] = useState<ChartKind>(chart.kind);
  const kinds = useMemo(() => {
    const compatible = KINDS.compatible(chart, table);
    return compatible.includes(chart.kind) ? compatible : [chart.kind, ...compatible];
  }, [chart, table]);

  useEffect(() => {
    if (element.current === null) {
      return;
    }
    const drawn = host.create(element.current);
    surface.current = drawn;
    const observer =
      typeof ResizeObserver === "undefined"
        ? null
        : new ResizeObserver(() => {
            drawn.resize();
          });
    observer?.observe(element.current);
    return () => {
      observer?.disconnect();
      drawn.dispose();
      surface.current = null;
    };
  }, [host]);

  useEffect(() => {
    surface.current?.setOption(BUILDER.build(chart, table, kind), true);
  }, [chart, table, kind, host]);

  return (
    <section className="result-chart">
      <div className="result-chart-header">
        <h3 className="result-title">{chart.title}</h3>
        {kinds.length > 1 && (
          <label className="result-chart-kind">
            Rodzaj wykresu{" "}
            <select
              value={kind}
              onChange={(event) => {
                const chosen = kinds.find((option) => option === event.target.value);
                if (chosen !== undefined) {
                  setKind(chosen);
                }
              }}
            >
              {kinds.map((option) => (
                <option key={option} value={option}>
                  {ChartKinds.label(option)}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      <div ref={element} className="result-chart-canvas" />
    </section>
  );
}
