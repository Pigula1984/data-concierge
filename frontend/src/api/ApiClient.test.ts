import { describe, expect, it, vi } from "vitest";

import { ApiClient, FAILURE_MESSAGE, type AskResponse } from "./ApiClient";

const ANSWER: AskResponse = {
  status: "ok",
  session_id: "s1",
  message: "Gotowe.",
  tables: [],
  assumptions: [],
  clarification: null,
  provenance: [],
};

function sentBody(init: RequestInit | undefined): unknown {
  const body = init?.body;
  if (typeof body !== "string") {
    throw new Error("the request has no JSON text body");
  }
  return JSON.parse(body);
}

function jsonReply(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function clientReplying(reply: Response | Error) {
  const fetchFn = vi.fn<typeof fetch>(() =>
    reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply),
  );
  return { client: new ApiClient(fetchFn), fetchFn };
}

describe("ApiClient.ask", () => {
  it("posts the prompt, user and missing session as JSON to /ask", async () => {
    const { client, fetchFn } = clientReplying(jsonReply(200, ANSWER));

    await client.ask({ prompt: "Raport", user_id: 7, session_id: null });

    const [url, init] = fetchFn.mock.calls[0] ?? [];
    expect(url).toBe("/ask");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(sentBody(init)).toEqual({
      prompt: "Raport",
      user_id: 7,
      session_id: null,
    });
  });

  it("sends the session id it is given", async () => {
    const { client, fetchFn } = clientReplying(jsonReply(200, ANSWER));

    await client.ask({ prompt: "Dalej", user_id: 7, session_id: "s1" });

    const init = fetchFn.mock.calls[0]?.[1];
    expect(sentBody(init)).toMatchObject({ session_id: "s1" });
  });

  it("returns a response with status error as a response, not a failure", async () => {
    const error = { ...ANSWER, status: "error", message: "Sesja s1 wygasła." };
    const { client } = clientReplying(jsonReply(200, error));

    const result = await client.ask({ prompt: "Dalej", user_id: 7, session_id: "s1" });

    expect(result).toEqual({ kind: "response", response: error });
  });

  it("returns a rejected prompt with the backend's reason on 422 with a text detail", async () => {
    const reason = "Prośba jest za długa: limit to 4000 znaków.";
    const { client } = clientReplying(jsonReply(422, { detail: reason }));

    const result = await client.ask({ prompt: "x", user_id: 7, session_id: null });

    expect(result).toEqual({ kind: "rejected", message: reason });
  });

  it("returns a failure on 422 with a validation list", async () => {
    const detail = [{ loc: ["body", "prompt"], msg: "Field required", type: "missing" }];
    const { client } = clientReplying(jsonReply(422, { detail }));

    const result = await client.ask({ prompt: "x", user_id: 7, session_id: null });

    expect(result).toEqual({ kind: "failed", message: FAILURE_MESSAGE });
  });

  it.each([
    ["a server error", new Response("Internal Server Error", { status: 500 })],
    ["a body that is not JSON", new Response("<html>proxy</html>", { status: 200 })],
    ["a network error", new TypeError("Failed to fetch")],
  ])("returns a failure on %s", async (_case, reply) => {
    const { client } = clientReplying(reply);

    const result = await client.ask({ prompt: "x", user_id: 7, session_id: null });

    expect(result).toEqual({ kind: "failed", message: FAILURE_MESSAGE });
  });
});
