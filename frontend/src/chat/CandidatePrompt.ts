import type { Candidate } from "../api/ApiClient";

/**
 * The prompt a candidate button sends. It names the candidate by id only: the description comes
 * from columns that may be sensitive and is meant for the user, not for the model.
 */
export class CandidatePrompt {
  forCandidate(candidate: Candidate): string {
    return `Wybieram ${String(candidate.id)}.`;
  }
}
