"use client";

import { useEffect, useState } from "react";
import { duration } from "@/lib/format";

/**
 * A time that keeps moving.
 *
 * The server renders a value and this takes over on the client, so the page is
 * correct before hydration and stays correct after. It counts down to a fixed
 * instant rather than decrementing a number, so a slow tab or a sleeping laptop
 * does not leave it reporting a time that quietly stopped being true.
 */
export function Countdown({
  target,
  className = "numeric",
}: {
  target: string | null;
  className?: string;
}) {
  const compute = () =>
    target ? (new Date(target).getTime() - Date.now()) / 1000 : null;

  const [seconds, setSeconds] = useState<number | null>(compute);

  useEffect(() => {
    if (!target) return;
    setSeconds(compute());
    const timer = setInterval(() => setSeconds(compute()), 1000);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  if (seconds === null) return <span className={className}>--</span>;

  return (
    <span
      className={className}
      style={seconds < 0 ? { color: "var(--cds-text-error)" } : undefined}
      title={target ?? undefined}
    >
      {duration(seconds)}
    </span>
  );
}
