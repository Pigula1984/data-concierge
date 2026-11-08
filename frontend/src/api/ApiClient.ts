import type { components } from "./schema";

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

/** A turn ends with a response (any status), a prompt the backend refused, or a failure. */
export type TurnResult =
  | { kind: "response"; response: AskResponse }
  | { kind: "rejected"; message: string }
  | { kind: "failed"; message: string };

export const FAILURE_MESSAGE = "Nie udało się połączyć z asystentem. Spróbuj ponownie za chwilę.";

const UNPROCESSABLE = 422;

/** Sends one turn to `POST /ask`; the conversation history stays in the backend session. */
export class ApiClient {
  private readonly fetchFn: typeof fetch;
  private readonly baseUrl: string;

  // The default wraps the global fetch: called as a method, an unbound fetch throws.
  constructor(fetchFn: typeof fetch = (input, init) => fetch(input, init), baseUrl = "") {
    this.fetchFn = fetchFn;
    this.baseUrl = baseUrl;
  }

  async ask(request: AskRequest): Promise<TurnResult> {
    try {
      const reply = await this.fetchFn(`${this.baseUrl}/ask`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(request),
      });
      return await this.toResult(reply);
    } catch {
      return { kind: "failed", message: FAILURE_MESSAGE };
    }
  }

  private async toResult(reply: Response): Promise<TurnResult> {
    if (reply.ok) {
      // The body is the backend's response contract, typed from its OpenAPI schema.
      return { kind: "response", response: (await reply.json()) as AskResponse };
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
}
