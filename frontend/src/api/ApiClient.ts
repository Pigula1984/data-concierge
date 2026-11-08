import type { components } from "./schema";
import { SseParser } from "./SseParser";

export type AskRequest = components["schemas"]["AskRequest"];
export type AskResponse = components["schemas"]["Response"];
export type ResponseTable = components["schemas"]["ResponseTable"];
export type ResponseColumn = components["schemas"]["ResponseColumn"];
export type ResponseChart = components["schemas"]["ResponseChart"];
export type ChartKind = components["schemas"]["ChartKind"];
export type Candidate = components["schemas"]["Candidate"];
export type ProvenanceStep = components["schemas"]["ProvenanceStep"];
export type AnswerStatus = components["schemas"]["AnswerStatus"];
export type CellValue = components["schemas"]["JsonScalar"];
export type TurnStep = components["schemas"]["ProgressEvent"];
type StreamItem = components["schemas"]["StreamItem"];

/** A turn ends with a response (any status), a prompt the backend refused, or a failure. */
export type TurnResult =
  | { kind: "response"; response: AskResponse }
  | { kind: "rejected"; message: string }
  | { kind: "failed"; message: string };

export const FAILURE_MESSAGE = "Nie udało się połączyć z asystentem. Spróbuj ponownie za chwilę.";

const UNPROCESSABLE = 422;

/**
 * Sends one turn to `POST /ask/stream`; the conversation history stays in the backend session.
 * The stream gives each step of the turn as it begins, then the same response as `POST /ask`.
 */
export class ApiClient {
  private readonly fetchFn: typeof fetch;
  private readonly baseUrl: string;

  // The default wraps the global fetch: called as a method, an unbound fetch throws.
  constructor(fetchFn: typeof fetch = (input, init) => fetch(input, init), baseUrl = "") {
    this.fetchFn = fetchFn;
    this.baseUrl = baseUrl;
  }

  async ask(
    request: AskRequest,
    onStep: (step: TurnStep) => void = () => undefined,
  ): Promise<TurnResult> {
    try {
      const reply = await this.fetchFn(`${this.baseUrl}/ask/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      return await this.toResult(reply, onStep);
    } catch {
      return { kind: "failed", message: FAILURE_MESSAGE };
    }
  }

  private async toResult(reply: Response, onStep: (step: TurnStep) => void): Promise<TurnResult> {
    if (reply.ok && reply.body !== null) {
      return await this.read(reply.body, onStep);
    }
    if (reply.status === UNPROCESSABLE) {
      const body: unknown = await reply.json();
      const detail: unknown =
        typeof body === "object" && body !== null ? Reflect.get(body, "detail") : null;
      if (typeof detail === "string") {
        return { kind: "rejected", message: detail };
      }
    }
    return { kind: "failed", message: FAILURE_MESSAGE };
  }

  /** A stream that ends without a response failed on the way, e.g. the server stopped. */
  private async read(
    body: ReadableStream<Uint8Array>,
    onStep: (step: TurnStep) => void,
  ): Promise<TurnResult> {
    const reader = body.getReader();
    const decoder = new TextDecoder();
    const parser = new SseParser();
    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) {
          return { kind: "failed", message: FAILURE_MESSAGE };
        }
        for (const event of parser.push(decoder.decode(value, { stream: true }))) {
          // The data is the backend's stream item, typed from its OpenAPI schema.
          const item = JSON.parse(event.data) as StreamItem;
          if ("step" in item) {
            onStep(item);
          } else {
            return { kind: "response", response: item };
          }
        }
      }
    } finally {
      reader.releaseLock();
      // The response has arrived; the rest of the stream, if any, is not needed.
      void body.cancel().catch(() => undefined);
    }
  }
}
