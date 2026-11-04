import { AnswerView } from "./AnswerView";
import { CandidatePrompt } from "./CandidatePrompt";
import { ElapsedTimer } from "./ElapsedTimer";
import { PromptForm } from "./PromptForm";
import { useConversation, type TurnClient } from "./useConversation";

const CANDIDATE_PROMPT = new CandidatePrompt();

export function ChatView({ client, userId }: { client: TurnClient; userId: number }) {
  const { entries, pending, send, reset } = useConversation(client, userId);
  const lastId = entries.at(-1)?.id;

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
                  canChoose={entry.id === lastId && !pending}
                  onChoose={(candidate) => {
                    void send(CANDIDATE_PROMPT.forCandidate(candidate));
                  }}
                  onNewConversation={reset}
                />
              );
          }
        })}
      </div>
      {pending && <ElapsedTimer />}
      <PromptForm disabled={pending} onSend={send} />
    </section>
  );
}
