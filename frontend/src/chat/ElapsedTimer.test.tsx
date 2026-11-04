import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ElapsedTimer } from "./ElapsedTimer";

describe("ElapsedTimer", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("counts the seconds since the turn was sent", () => {
    render(<ElapsedTimer />);
    expect(screen.getByRole("status")).toHaveTextContent("0 s");

    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByRole("status")).toHaveTextContent("3 s");
  });
});
