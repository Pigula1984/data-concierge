import { AnswerView } from "./AnswerView";
import { CandidatePrompt } from "./CandidatePrompt";
import { PromptForm } from "./PromptForm";
import { TurnProgressView } from "./TurnProgressView";
import { useConversation, type TurnClient } from "./useConversation";

const CANDIDATE_PROMPT = new CandidatePrompt();

export function ChatView({ client, userId }: { client: TurnClient; userId: number }) {
  const { entries, pending, steps, send, reset } = useConversation(client, userId);
  // Only a newer answer locks the candidates; a failed or refused turn leaves them to retry.
  const lastAnswerId = entries.findLast((entry) => entry.kind === "answer")?.id;

  return (
    <section className="chat">
      <div className="chat-toolbar">
        <button type="button" disabled={pending || entries.length === 0} onClick={reset}>
          Nowa rozmowa
        </button>
      </div>
      <div className="chat-log" aria-live="polite">
        {entries.map((entry) => {
          switch (entry.kind) {
            case "prompt":
              return (
                <p key={entry.id} className="prompt">
                  {entry.text}
                </p>
              );
            case "notice":
              return (
                <p key={entry.id} className="notice" role="alert">
                  {entry.text}
                </p>
              );
            case "answer":
              return (
                <AnswerView
                  key={entry.id}
                  response={entry.response}
                  canChoose={entry.id === lastAnswerId && !pending}
                  pending={pending}
                  onChoose={(candidate) => {
                    void send(CANDIDATE_PROMPT.forCandidate(candidate));
                  }}
                  onNewConversation={reset}
                />
              );
          }
        })}
      </div>
      {pending && <TurnProgressView steps={steps} />}
      <PromptForm disabled={pending} onSend={send} />
    </section>
  );
}
