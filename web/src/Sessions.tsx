import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/**
 * Scanning sessions — the batch being worked on.
 *
 * Two thousand cards is not one sitting, and "what am I working on" needs an answer narrower
 * than the whole collection. New scans join the open session; starting another closes the
 * current one without touching its cards, and an old one can be reopened to add to it.
 *
 * Deleting a session keeps its cards by default. Removing a grouping is not the same as
 * removing photographs of real cards, and defaulting the other way is how an evening's work
 * disappears during a tidy-up.
 */
export default function Sessions({ onChange }: { onChange?: () => void }) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.sessions>> | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.sessions());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
      onChange?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const current = data?.sessions.find((s) => s.current);

  return (
    <div className="sessions">
      <div className="sessions-head">
        <span className="field-label">Scanning session</span>
        <strong>{current ? current.name : "none yet"}</strong>
        {current ? <span className="muted">{current.cards} cards</span> : null}
        {current?.photos_only ? (
          <span className="ok-chip">photos only — not identifying</span>
        ) : null}
        {current && current.cards > 0 ? (
          <a
            className="chip"
            href={`/api/sessions/${current.id}/photos.zip`}
            download
            title="Front, back and corner close-ups for every card, named in upload order"
          >
            Download photos (.zip)
          </a>
        ) : null}
        <button
          disabled={busy}
          onClick={() => {
            const name = window.prompt(
              "Name this batch (or leave blank for today's date)",
              "",
            );
            if (name === null) return;
            act(() => api.startSession(name.trim() || undefined));
          }}
        >
          Start a new batch
        </button>
        <button
          disabled={busy}
          title="Crop and render photographs, but do not identify or price. For batches going to another listing tool."
          onClick={() => {
            const name = window.prompt(
              "Name this photos-only batch",
              "Photos for uploader",
            );
            if (name === null) return;
            act(() => api.startSession(name.trim() || undefined, true));
          }}
        >
          New photos-only batch
        </button>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {data && data.sessions.length > 1 ? (
        <table className="sessions-table">
          <tbody>
            {data.sessions.map((s) => (
              <tr key={s.id} className={s.current ? "is-current" : ""}>
                <td>
                  {s.name}
                  {s.current ? <span className="ok-chip">scanning into this</span> : null}
                  {s.photos_only ? <span className="warn-chip">photos only</span> : null}
                </td>
                <td className="muted">{new Date(s.started_at).toLocaleDateString()}</td>
                <td className="muted">{s.cards} cards</td>
                <td>
                  {s.cards > 0 ? (
                    <a className="chip" href={`/api/sessions/${s.id}/photos.zip`} download>
                      photos
                    </a>
                  ) : null}
                  {!s.current ? (
                    <button disabled={busy} onClick={() => act(() => api.reopenSession(s.id))}>
                      scan into this
                    </button>
                  ) : null}
                  <button
                    disabled={busy}
                    onClick={() => {
                      if (
                        !window.confirm(
                          `Remove the batch "${s.name}"?\n\n` +
                            `Its ${s.cards} cards are KEPT — only the grouping goes.`,
                        )
                      )
                        return;
                      act(() => api.deleteSession(s.id, "keep"));
                    }}
                  >
                    remove batch
                  </button>
                  {s.cards > 0 ? (
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={() => {
                        if (
                          !window.confirm(
                            `DELETE "${s.name}" AND its ${s.cards} cards?\n\n` +
                              "The photographs are deleted too. This cannot be undone — " +
                              "run `make backup` first if you are unsure.",
                          )
                        )
                          return;
                        act(() => api.deleteSession(s.id, "delete"));
                      }}
                    >
                      delete cards too
                    </button>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      {data && data.unassigned > 0 ? (
        <p className="muted">
          {data.unassigned} cards are not in any batch — scanned before batches existed, or their
          batch was removed. They still appear everywhere else.
        </p>
      ) : null}
    </div>
  );
}
