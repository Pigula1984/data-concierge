import { useEffect, useState } from "react";

const SECOND = 1000;

/** Seconds since the turn was sent; a turn takes several seconds of model rounds. */
export function ElapsedTimer() {
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
    <p className="elapsed" role="status">
      Czekam na odpowiedź… {seconds} s
    </p>
  );
}
