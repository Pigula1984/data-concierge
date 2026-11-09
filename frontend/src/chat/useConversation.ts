import { useCallback, useRef, useState } from "react";

import type { AskRequest, AskResponse, TurnResult, TurnStep } from "../api/ApiClient";

/** Sends one turn; `ApiClient` in the app, a scripted client in tests. */
export interface TurnClient {
  ask(request: AskRequest, onStep: (step: TurnStep) => void): Promise<TurnResult>;
}

export type ChatEntry =
  | { kind: "prompt"; id: number; text: string }
  | { kind: "answer"; id: number; response: AskResponse }
  | { kind: "notice"; id: number; text: string };

export interface Conversation {
  entries: readonly ChatEntry[];
  pending: boolean;
  /** The steps of the running turn, oldest first; the same step twice in a row shows once. */
  steps: readonly string[];
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
  const [steps, setSteps] = useState<readonly string[]>([]);
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
      const result = await client.ask(
        { prompt: text, user_id: userId, session_id: sessionId.current },
        (step) => {
          setSteps((current) => (current.at(-1) === step.text ? current : [...current, step.text]));
        },
      );
      setPending(false);
      setSteps([]);
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

  // A turn that is still running would write its session back after the reset, so the new
  // conversation would continue the old one; resetting waits until the turn ends.
  const reset = useCallback(() => {
    if (pending) {
      return;
    }
    sessionId.current = null;
    setEntries([]);
  }, [pending]);

  return { entries, pending, steps, send, reset };
}
