import { describe, expect, it, vi } from "vitest";

import { ApiClient, FAILURE_MESSAGE, type AskResponse, type TurnStep } from "./ApiClient";

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

const GUARD: TurnStep = { step: "guard", text: "Sprawdzam prośbę…" };
const ROUND: TurnStep = { step: "model", text: "Analizuję prośbę…" };

/** A stream body that sends the given chunks of text, then fails when `failure` is given. */
function streamReply(chunks: string[], failure?: Error): Response {
  const encoder = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      if (failure) {
        controller.error(failure);
      } else {
        controller.close();
      }
    },
  });
  return new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

function events(...items: unknown[]): string[] {
  return items.map((item) => `data: ${JSON.stringify(item)}\n\n`);
}

function clientReplying(reply: Response | Error) {
  const fetchFn = vi.fn<typeof fetch>(() =>
    reply instanceof Error ? Promise.reject(reply) : Promise.resolve(reply),
  );
  return { client: new ApiClient(fetchFn), fetchFn };
}

describe("ApiClient.ask", () => {
  it("posts the prompt, user and missing session as JSON to /ask/stream", async () => {
    const { client, fetchFn } = clientReplying(streamReply(events(ANSWER)));

    await client.ask({ prompt: "Raport", user_id: 7, session_id: null });

    const [url, init] = fetchFn.mock.calls[0] ?? [];
    expect(url).toBe("/ask/stream");
    expect(init?.method).toBe("POST");
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    expect(sentBody(init)).toEqual({
      prompt: "Raport",
      user_id: 7,
      session_id: null,
    });
  });

  it("sends the session id it is given", async () => {
    const { client, fetchFn } = clientReplying(streamReply(events(ANSWER)));

    await client.ask({ prompt: "Dalej", user_id: 7, session_id: "s1" });

    const init = fetchFn.mock.calls[0]?.[1];
    expect(sentBody(init)).toMatchObject({ session_id: "s1" });
  });

  it("returns a response with status error as a response, not a failure", async () => {
    const error = { ...ANSWER, status: "error", message: "Sesja s1 wygasła." };
    const { client } = clientReplying(streamReply(events(error)));

    const result = await client.ask({ prompt: "Dalej", user_id: 7, session_id: "s1" });

    expect(result).toEqual({ kind: "response", response: error });
  });

  it("passes each step to the listener as it arrives, then returns the response", async () => {
    const [guard = "", round = "", answer = ""] = events(GUARD, ROUND, ANSWER);
    // The stream splits an event in the middle and sends a keepalive comment between steps.
    const chunks = [guard.slice(0, 9), guard.slice(9) + ": ping\n\n", round + answer];
    const { client } = clientReplying(streamReply(chunks));
    const steps: TurnStep[] = [];

    const result = await client.ask({ prompt: "Raport", user_id: 7, session_id: null }, (step) =>
      steps.push(step),
    );

    expect(steps).toEqual([GUARD, ROUND]);
    expect(result).toEqual({ kind: "response", response: ANSWER });
  });

  it("decodes a Polish step split inside a multi-byte character", async () => {
    const step: TurnStep = { step: "procedure", text: "Pobieram zamówienia…" };
    const bytes = new TextEncoder().encode(events(step, ANSWER).join(""));
    const text = new TextDecoder().decode(bytes);
    // Cut inside the two bytes of "ó" and inside the three bytes of "…".
    const insideO = new TextEncoder().encode(text.slice(0, text.indexOf("ó"))).length + 1;
    const insideEllipsis = new TextEncoder().encode(text.slice(0, text.indexOf("…"))).length + 2;
    const body = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(bytes.slice(0, insideO));
        controller.enqueue(bytes.slice(insideO, insideEllipsis));
        controller.enqueue(bytes.slice(insideEllipsis));
        controller.close();
      },
    });
    const { client } = clientReplying(new Response(body, { status: 200 }));
    const steps: TurnStep[] = [];

    await client.ask({ prompt: "Raport", user_id: 7, session_id: null }, (s) => steps.push(s));

    expect(steps).toEqual([step]);
  });

  it("returns a failure when the stream ends without a response", async () => {
    const { client } = clientReplying(streamReply(events(GUARD)));

    const result = await client.ask({ prompt: "Raport", user_id: 7, session_id: null });

    expect(result).toEqual({ kind: "failed", message: FAILURE_MESSAGE });
  });

  it("returns a failure when the connection breaks while the turn runs", async () => {
    const { client } = clientReplying(streamReply(events(GUARD), new TypeError("network")));

    const result = await client.ask({ prompt: "Raport", user_id: 7, session_id: null });

    expect(result).toEqual({ kind: "failed", message: FAILURE_MESSAGE });
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
