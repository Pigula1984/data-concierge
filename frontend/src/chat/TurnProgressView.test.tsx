import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TurnProgressView } from "./TurnProgressView";

describe("TurnProgressView", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts the seconds since the turn was sent", () => {
    render(<TurnProgressView steps={[]} />);
    expect(screen.getByRole("status")).toHaveTextContent("0 s");

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByRole("status")).toHaveTextContent("3 s");
  });

  it("lists the steps with the last one as the current step", () => {
    render(<TurnProgressView steps={["Sprawdzam prośbę…", "Analizuję prośbę…"]} />);

    const items = screen.getAllByRole("listitem");
    expect(items.map((item) => item.textContent)).toEqual([
      "Sprawdzam prośbę…",
      "Analizuję prośbę…",
    ]);
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[0]).not.toHaveAttribute("aria-current");
  });

  it("shows a step that looks like HTML as text", () => {
    const { container } = render(<TurnProgressView steps={["<img src=x onerror=alert(1)>"]} />);

    expect(screen.getByRole("listitem")).toHaveTextContent("<img src=x onerror=alert(1)>");
    expect(container.querySelector("img")).toBeNull();
  });
});
