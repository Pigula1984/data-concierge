import type { Candidate } from "../api/ApiClient";
import type { components } from "../api/schema";

type Clarification = components["schemas"]["Clarification"];

interface ClarificationViewProps {
  clarification: Clarification;
  enabled: boolean;
  onChoose: (candidate: Candidate) => void;
}

export function ClarificationView({ clarification, enabled, onChoose }: ClarificationViewProps) {
  return (
    <div className="clarification">
      <p className="clarification-question">{clarification.question}</p>
      {clarification.candidates.length > 0 && (
        <div className="candidates">
          {clarification.candidates.map((candidate) => (
            <button
              key={String(candidate.id)}
              type="button"
              disabled={!enabled}
              onClick={() => {
                onChoose(candidate);
              }}
            >
              {candidate.description}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
