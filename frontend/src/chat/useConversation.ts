import { useCallback, useRef, useState } from "react";

import type { AskRequest, AskResponse, TurnResult } from "../api/ApiClient";

/** Sends one turn; `ApiClient` in the app, a scripted client in tests. */
export interface TurnClient {
  ask(request: AskRequest): Promise<TurnResult>;
}

export type ChatEntry =
  | { kind: "prompt"; id: number; text: string }
  | { kind: "answer"; id: number; response: AskResponse }
  | { kind: "notice"; id: number; text: string };

export interface Conversation {
  entries: readonly ChatEntry[];
  pending: boolean;
  /** Resolves true when the backend answered, false when the prompt should stay for a retry. */
  send: (prompt: string) => Promise<boolean>;
  reset: () => void;
}

/**
 * The conversation as the page shows it. The backend keeps the model's history in its session;
 * the page only sends the new prompt with the session id of the last answer.
 */
export function useConversation(client: TurnClient, userId: number): Conversation {
  const [entries, setEntries] = useState<readonly ChatEntry[]>([]);
  const [pending, setPending] = useState(false);
  const sessionId = useRef<string | null>(null);
  const nextId = useRef(0);

  const send = useCallback(
    async (prompt: string): Promise<boolean> => {
      const text = prompt.trim();
      if (text === "" || pending) {
        return false;
      }
      const promptId = nextId.current++;
      setEntries((current) => [...current, { kind: "prompt", id: promptId, text }]);
      setPending(true);
      const result = await client.ask({
        prompt: text,
        user_id: userId,
        session_id: sessionId.current,
      });
      setPending(false);
      const id = nextId.current++;
      if (result.kind === "response") {
        sessionId.current = result.response.session_id;
        setEntries((current) => [...current, { kind: "answer", id, response: result.response }]);
        return true;
      }
      // The prompt did not become part of the conversation: it stays in the form for a retry.
      setEntries((current) => [
        ...current.filter((entry) => entry.id !== promptId),
        { kind: "notice", id, text: result.message },
      ]);
      return false;
    },
    [client, userId, pending],
  );

  const reset = useCallback(() => {
    sessionId.current = null;
    setEntries([]);
  }, []);

  return { entries, pending, send, reset };
}
