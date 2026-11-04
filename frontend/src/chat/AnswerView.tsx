import type { AnswerStatus, AskResponse, Candidate } from "../api/ApiClient";
import { ResultGrid } from "../grid/ResultGrid";
import { ClarificationView } from "./ClarificationView";
import { ProvenanceView } from "./ProvenanceView";

const STATUS_LABELS: Record<AnswerStatus, string | null> = {
  ok: null,
  no_data: "Brak danych",
  needs_clarification: "Potrzebne doprecyzowanie",
  unsupported: "Poza zakresem",
  error: "Błąd",
};

interface AnswerViewProps {
  response: AskResponse;
  /** Candidates can be chosen only on the latest answer, and not while a turn runs. */
  canChoose: boolean;
  /** A turn is running: a new conversation would race with it. */
  pending: boolean;
  onChoose: (candidate: Candidate) => void;
  onNewConversation: () => void;
}

export function AnswerView({
  response,
  canChoose,
  pending,
  onChoose,
  onNewConversation,
}: AnswerViewProps) {
  const label = STATUS_LABELS[response.status];
  return (
    <article className={`answer answer--${response.status}`}>
      {label !== null && <p className="answer-status">{label}</p>}
      <p className="answer-message">{response.message}</p>
      {response.status === "error" && (
        <button type="button" disabled={pending} onClick={onNewConversation}>
          Nowa rozmowa
        </button>
      )}
      {response.tables.map((table) => (
        <ResultGrid key={table.handle} table={table} />
      ))}
      {response.assumptions.length > 0 && (
        <ul className="assumptions" aria-label="Założenia">
          {response.assumptions.map((assumption, index) => (
            // The same assumption may repeat; the list never reorders.
            <li key={index}>{assumption}</li>
          ))}
        </ul>
      )}
      {response.clarification && (
        <ClarificationView
          clarification={response.clarification}
          enabled={canChoose}
          onChoose={onChoose}
        />
      )}
      {response.provenance.length > 0 && <ProvenanceView steps={response.provenance} />}
    </article>
  );
}
