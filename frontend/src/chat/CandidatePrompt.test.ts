import { describe, expect, it } from "vitest";

import { CandidatePrompt } from "./CandidatePrompt";

describe("CandidatePrompt", () => {
  it("names the candidate by id and leaves its description out", () => {
    const prompt = new CandidatePrompt().forCandidate({
      id: 12698,
      description: "AW00012698, Australia, 3 zamówienia",
    });

    expect(prompt).toContain("12698");
    expect(prompt).not.toContain("AW00012698");
    expect(prompt).not.toContain("Australia");
  });

  it("works with a text id", () => {
    expect(new CandidatePrompt().forCandidate({ id: "BK-M68B-38", description: "x" })).toContain(
      "BK-M68B-38",
    );
  });
});
