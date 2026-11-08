import { useEffect, useState } from "react";

const SECOND = 1000;

/**
 * What the running turn is doing: its steps as the backend reports them, the current one last,
 * and the seconds since it was sent. A turn takes several seconds of model rounds.
 */
export function TurnProgressView({ steps }: { steps: readonly string[] }) {
  const [seconds, setSeconds] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setSeconds((value) => value + 1);
    }, SECOND);
    return () => {
      window.clearInterval(timer);
    };
  }, []);

  return (
    <div className="turn-progress" role="status">
      {steps.length > 0 && (
        <ol className="turn-progress__steps">
          {steps.map((text, index) => (
            // Steps are only appended, so a position names the same step for the whole turn.
            <li key={index} aria-current={index === steps.length - 1 ? "step" : undefined}>
              {text}
            </li>
          ))}
        </ol>
      )}
      <p className="elapsed">Czekam na odpowiedź… {seconds} s</p>
    </div>
  );
}
