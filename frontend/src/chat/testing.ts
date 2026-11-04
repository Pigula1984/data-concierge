import type { AskRequest, AskResponse, TurnResult } from "../api/ApiClient";
import type { TurnClient } from "./useConversation";

export function answer(overrides: Partial<AskResponse> = {}): AskResponse {
  return {
    status: "ok",
    session_id: "s1",
    message: "Gotowe.",
    tables: [],
    assumptions: [],
    clarification: null,
    provenance: [],
    ...overrides,
  };
}

/** A client that records each request and replies with the next scripted result. */
export class ScriptedClient implements TurnClient {
  readonly requests: AskRequest[] = [];
  private readonly replies: Promise<TurnResult>[];

  constructor(...replies: (TurnResult | Promise<TurnResult>)[]) {
    this.replies = replies.map((reply) => Promise.resolve(reply));
  }

  ask(request: AskRequest): Promise<TurnResult> {
    this.requests.push(request);
    return this.replies.shift() ?? Promise.reject(new Error("no scripted reply left"));
  }
}

/** A reply the test resolves later, to look at the page while a turn is running. */
export class PendingReply {
  readonly promise: Promise<TurnResult>;
  private settle: (result: TurnResult) => void = () => undefined;

  constructor() {
    this.promise = new Promise((resolve) => {
      this.settle = resolve;
    });
  }

  resolve(result: TurnResult): void {
    this.settle(result);
  }
}
