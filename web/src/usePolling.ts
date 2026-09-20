import { useEffect, useRef } from "react";

/**
 * Run something on an interval, but only while somebody is looking at it.
 *
 * Five screens each kept their own `setInterval`, and every one of them carried on at full
 * rate in a background tab. The batch screen alone was three requests every six seconds —
 * about 2,000 an hour into a tab nobody had in front of them, each one waking Python, hitting
 * Postgres and keeping the radio up. On a laptop that is the battery; on a Pi serving a phone
 * it is contention with the work that matters.
 *
 * Hidden means paused, not slowed. And becoming visible refreshes immediately rather than
 * waiting out the rest of the interval, because the first thing you do on returning to a tab
 * is look at it.
 */
export function usePolling(fn: () => void, ms: number, enabled = true) {
  // Kept in a ref so a caller passing an inline arrow does not restart the timer on every
  // render — which would mean it never actually fires.
  const saved = useRef(fn);
  saved.current = fn;

  useEffect(() => {
    if (!enabled) return;

    let timer: number | undefined;

    const stop = () => {
      if (timer !== undefined) {
        clearInterval(timer);
        timer = undefined;
      }
    };
    const start = () => {
      stop();
      timer = window.setInterval(() => saved.current(), ms);
    };

    const onVisibility = () => {
      if (document.hidden) {
        stop();
      } else {
        saved.current();
        start();
      }
    };

    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [ms, enabled]);
}
