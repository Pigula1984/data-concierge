import { BarChart, LineChart, PieChart, ScatterChart } from "echarts/charts";
import {
  GridComponent,
  LegendComponent,
  ToolboxComponent,
  TooltipComponent,
} from "echarts/components";
import { init, use as registerModules } from "echarts/core";
import { CanvasRenderer } from "echarts/renderers";
import { createContext } from "react";

import type { ChartOption } from "./ChartOptionsBuilder";

/** The part of a drawn chart the page uses. */
export interface ChartSurface {
  setOption(option: ChartOption, notMerge: boolean): void;
  resize(): void;
  dispose(): void;
}

/** Creates a chart in an element; tests replace it, since jsdom has no canvas. */
export interface ChartHost {
  create(element: HTMLElement): ChartSurface;
}

/** ECharts with only the charts and components the page draws (plan 3B, KTD5). */
export class EChartsHost implements ChartHost {
  private static registered = false;

  create(element: HTMLElement): ChartSurface {
    EChartsHost.register();
    return init(element, undefined, { renderer: "canvas" });
  }

  private static register(): void {
    if (EChartsHost.registered) {
      return;
    }
    registerModules([
      LineChart,
      BarChart,
      PieChart,
      ScatterChart,
      GridComponent,
      LegendComponent,
      ToolboxComponent,
      TooltipComponent,
      CanvasRenderer,
    ]);
    EChartsHost.registered = true;
  }
}

export const ChartHostContext = createContext<ChartHost>(new EChartsHost());
