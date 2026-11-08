import type { ProvenanceStep } from "../api/ApiClient";

/** How the tables were computed: each procedure call and each query, folded by default. */
export function ProvenanceView({ steps }: { steps: readonly ProvenanceStep[] }) {
  return (
    <details className="provenance">
      <summary>Jak policzono</summary>
      <ol>
        {steps.map((step) => (
          <li key={step.handle}>
            {step.step === "procedure" ? (
              <span>
                {step.handle}: procedura {step.name} {JSON.stringify(step.params)}, wierszy:{" "}
                {step.rows}
                {step.truncated ? " (obcięte limitem)" : ""}
              </span>
            ) : (
              <>
                <span>
                  {step.handle}: zapytanie nad {step.sources.join(", ")}, wierszy: {step.rows}
                </span>
                <pre>{step.sql}</pre>
              </>
            )}
          </li>
        ))}
      </ol>
    </details>
  );
}
