import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/**
 * The backlog, by pipeline stage.
 *
 * "How many cards have I scanned" stops being the useful number almost immediately. What
 * matters at two thousand cards is where the queue is: everything photographed but not
 * identified, identified but without a variant, ready but ungraded. Those are different piles
 * of work and only one of them is ever the next thing to do.
 *
 * Derived from the data rather than from the review queue, deliberately. A card with no variant
 * and no review open yet is still outstanding work — counting only open reviews would report it
 * as done.
 */

const LABELS: Record<string, string> = {
  identification: "Which card is it",
  variant: "Which variant",
  image: "Image quality",
  condition: "Condition",
};

export default function Progress() {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.progress>> | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await api.progress());
    } catch {
      /* the dashboard must not break because one panel failed */
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 5000);
    return () => clearInterval(timer);
  }, [refresh]);

  if (!data) {
    return (
      <div className="card">
        <h2>Progress</h2>
        <span className="note">Loading…</span>
      </div>
    );
  }

  const { total, stages, blockers, next_up } = data;
  const blockerRows = Object.entries(blockers).filter(([, v]) => v.open + v.later > 0);

  return (
    <div className="card progress-card">
      <h2>Progress</h2>
      {total === 0 ? (
        <span className="note">Nothing scanned yet.</span>
      ) : (
        <>
          <p className="progress-lead">
            {total} card{total === 1 ? "" : "s"}
            {next_up ? (
              <>
                {" · next up: "}
                <strong>{next_up}</strong>
              </>
            ) : (
              " · all stages complete"
            )}
          </p>

          {stages.map((s) => {
            const pct = total ? Math.round((s.done / total) * 100) : 0;
            return (
              <div className="progress-row" key={s.name}>
                <div className="progress-label">
                  <span>{s.name}</span>
                  <span className="muted">
                    {s.done}/{total}
                    {s.outstanding > 0 ? ` · ${s.outstanding} to do` : ""}
                  </span>
                </div>
                {/* The bar is the backlog made visible: the unfilled part is the work left. */}
                <div className="progress-bar" role="img" aria-label={`${pct}% complete`}>
                  <div
                    className={s.outstanding === 0 ? "fill done" : "fill"}
                    style={{ width: `${pct}%` }}
                  />
                </div>
              </div>
            );
          })}

          {blockerRows.length > 0 ? (
            <div className="progress-blockers">
              <h3>Waiting on you</h3>
              {blockerRows.map(([category, counts]) => (
                <div className="row" key={category}>
                  <span>{LABELS[category] ?? category}</span>
                  <span>
                    {counts.open}
                    {counts.later > 0 ? (
                      <span className="muted"> · {counts.later} later</span>
                    ) : null}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </>
      )}
    </div>
  );
}
