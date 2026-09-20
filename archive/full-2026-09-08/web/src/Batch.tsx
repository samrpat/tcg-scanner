import { useCallback, useEffect, useState } from "react";
import { api, type InventoryRow, type ItemDetail } from "./api";
import CornerAdjuster from "./CornerAdjuster";

/**
 * The scanner.
 *
 * Everything here serves one loop: shoot a pile of cards, check the crops came out, hand the
 * folder to whatever is doing the listing, and start the next pile. Identification and pricing
 * are deliberately absent — another tool is doing those, and the rendering is the half it
 * cannot do.
 *
 * Cards are shown large because the whole product is the photograph. A thumbnail grid would
 * make this screen tidier and would hide exactly the flaw it exists to catch: a crop that
 * clipped a corner, or a card shot back-first.
 */
export default function Batch() {
  const [cards, setCards] = useState<InventoryRow[]>([]);
  const [session, setSession] = useState<Awaited<ReturnType<typeof api.sessions>> | null>(
    null,
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);
  // Editing is a mode, not a set of controls sitting on every card. The screen's job is to let
  // a pile go past quickly; handles and sliders on each card would fight that.
  const [editing, setEditing] = useState<string | null>(null);
  // Which batch is on screen. Null means the one being scanned into; an id means an earlier
  // batch the operator opened. Archiving is supposed to clear the screen, and it could not
  // while this view showed the whole collection regardless of batch.
  const [viewing, setViewing] = useState<string | null>(null);
  // Corner close-ups quadruple the file count and the upload time. Worth it on a card someone
  // will zoom into, dead weight on a bulk common — so it is a switch, remembered.
  const [corners, setCorners] = useState(() => {
    try {
      return localStorage.getItem("batch-corners") !== "off";
    } catch {
      return true;
    }
  });
  const setCornersRemembered = (on: boolean) => {
    setCorners(on);
    try {
      localStorage.setItem("batch-corners", on ? "on" : "off");
    } catch {
      /* private browsing; the switch still works for this session */
    }
  };

  const load = useCallback(async () => {
    try {
      const ses = await api.sessions();
      setSession(ses);
      const shown = viewing ?? ses.current_id;
      // Only this batch. Anything else on screen after archiving would mean the archive did
      // nothing, which is the whole of what "start the next pile" is supposed to do.
      const inv = shown
        ? await api.inventory(undefined, false, { session_id: shown })
        : { cards: [] };
      setCards(inv.cards);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [viewing]);

  useEffect(() => {
    load();
    // Cards appear as they finish processing, so this refreshes while a pile is being shot.
    // Cards appear as they finish processing, so this refreshes while a pile is being shot —
    // but an archived batch is finished by definition and does not need polling.
    if (viewing) return;
    const timer = setInterval(load, 6000);
    return () => clearInterval(timer);
  }, [load, viewing]);

  const current = session?.sessions.find((s) => s.current);
  const shownBatch = viewing
    ? session?.sessions.find((s) => s.id === viewing)
    : current;
  const isArchivedView = Boolean(viewing && viewing !== current?.id);
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="batch">
      <div className="batch-bar">
        <div>
          <span className="field-label">
            {isArchivedView ? "Viewing archived batch" : "Scanning into"}
          </span>
          <strong>{shownBatch?.name ?? "no batch yet"}</strong>
          <span className="muted"> · {cards.length} cards</span>
        </div>
        {isArchivedView ? (
          <button onClick={() => setViewing(null)}>back to the current batch</button>
        ) : null}
        <span className="spacer" />
        {shownBatch && cards.length > 0 ? (
          <>
            <a
              className="primary-link"
              href={`/api/sessions/${shownBatch?.id}/photos.zip?corners=${corners}`}
              download
            >
              Download all photos (.zip) ↓
            </a>
            <label className="chip" title="Four extra images per card, for buyers who zoom in">
              <input
                type="checkbox"
                checked={corners}
                onChange={(e) => setCornersRemembered(e.target.checked)}
              />
              corner close-ups
            </label>
            {!corners ? (
              <button
                className="linklike"
                disabled={busy}
                title="Delete the corner images already on disk"
                onClick={() => {
                  if (
                    !window.confirm(
                      "Delete every corner close-up already made?\n\n" +
                        "They can be cut again at any time — nothing original is lost.",
                    )
                  )
                    return;
                  act(async () => {
                    const r = await api.removeCornerDetails();
                    window.alert(`Removed ${r.removed} corner images.`);
                  });
                }}
              >
                remove the ones already made
              </button>
            ) : null}
            {!isArchivedView && current ? (
              <button
                disabled={busy}
                onClick={() => {
                  if (
                    !window.confirm(
                      `Archive "${current.name}" with its ${cards.length} cards, and start a ` +
                        "fresh batch?\n\nThe photographs stay — this screen clears, and they " +
                        "are under Earlier batches whenever you want them.",
                    )
                  )
                    return;
                  act(() => api.archiveSession(current.id));
                }}
              >
                Archive &amp; start next
              </button>
            ) : null}
          </>
        ) : null}
      </div>

      {error ? <p className="error">{error}</p> : null}

      {cards.length === 0 && !isArchivedView ? (
        <p className="muted">
          Nothing scanned into this batch yet. Use <strong>Scan</strong> on a phone or{" "}
          <strong>Capture</strong> on this machine — front then back, and they pair themselves.
        </p>
      ) : null}

      <div className="batch-grid">
        {cards.map((c) => (
          <div
            className={
              open === c.sku || editing === c.sku ? "batch-card expanded" : "batch-card"
            }
            key={c.sku}
          >
            <button
              className="batch-shot"
              title="See it large"
              onClick={() => setOpen(open === c.sku ? null : c.sku)}
            >
              {c.thumbnail ? (
                // A 400px copy, not the 2.7 MB original: a grid of twenty cards was fetching
                // fifty megabytes to draw pictures a few hundred pixels wide.
                <img src={`${c.thumbnail}&w=400`} alt={c.sku} loading="lazy" />
              ) : (
                <span>processing…</span>
              )}
            </button>
            <div className="batch-meta">
              <strong>{c.sku}</strong>
              {c.name ? <span className="muted"> {c.name}</span> : null}
            </div>
            <div className="batch-actions">
              <button
                className="linklike"
                disabled={busy}
                title="Front and back were shot the wrong way round"
                onClick={() => {
                  if (!window.confirm(`Swap ${c.sku}'s front and back?`)) return;
                  act(() => api.swapSides(c.sku));
                }}
              >
                swap sides
              </button>
              <button
                className="linklike danger-link"
                disabled={busy}
                onClick={() => {
                  if (
                    !window.confirm(
                      `Delete ${c.sku}?\n\nIts photographs go too. This cannot be undone.`,
                    )
                  )
                    return;
                  act(() => api.deleteCards([c.sku]));
                }}
              >
                delete
              </button>
            </div>
            {open === c.sku ? (
              <BatchPhotos
                sku={c.sku}
                corners={corners}
                onEdit={() => setEditing(c.sku)}
              />
            ) : null}
            {editing === c.sku ? (
              <CardEditor
                sku={c.sku}
                onClose={() => setEditing(null)}
                onChanged={load}
              />
            ) : null}
          </div>
        ))}
      </div>

      {session && session.sessions.length > 1 ? (
        <section className="batch-archive">
          <h3>Earlier batches</h3>
          <table className="sessions-table">
            <tbody>
              {session.sessions
                .filter((s) => !s.current)
                .map((s) => (
                  <tr key={s.id}>
                    <td>{s.name}</td>
                    <td className="muted">{new Date(s.started_at).toLocaleDateString()}</td>
                    <td className="muted">{s.cards} cards</td>
                    <td>
                      {s.cards > 0 ? (
                        <button disabled={busy} onClick={() => setViewing(s.id)}>
                          open
                        </button>
                      ) : null}
                      {s.cards > 0 ? (
                        <a
                          className="chip"
                          href={`/api/sessions/${s.id}/photos.zip?corners=${corners}`}
                          download
                        >
                          photos
                        </a>
                      ) : null}
                      <button disabled={busy} onClick={() => act(() => api.reopenSession(s.id))}>
                        scan into this
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      ) : null}
    </div>
  );
}

/** Every photograph of one card, at the size the flaws are visible at. */
function BatchPhotos({
  sku,
  corners,
  onEdit,
}: {
  sku: string;
  corners: boolean;
  onEdit: () => void;
}) {
  const shots = [
    ["listing-front", "front"],
    ["listing-back", "back"],
    ...(corners
      ? [
          ["detail-front-tl", "top left"],
          ["detail-front-tr", "top right"],
          ["detail-front-bl", "bottom left"],
          ["detail-front-br", "bottom right"],
        ]
      : []),
  ];
  return (
    <div className="batch-photos">
      {shots.map(([file, label]) => (
        <figure key={file}>
          <img
            src={`/api/images/${sku}/${file}.jpg?w=800`}
            alt={`${sku} ${label}`}
            loading="lazy"
          />
          <figcaption className="muted">{label}</figcaption>
        </figure>
      ))}
      <button className="linklike" onClick={onEdit}>
        edit crop &amp; corners
      </button>
    </div>
  );
}

/**
 * Editing one card, behind a mode.
 *
 * Two different things live here and they are easy to confuse, so they are labelled by what
 * they fix rather than by what they are: where the card's edges are (which decides the whole
 * crop), and how close the corner shots sit (which decides only those four images).
 */
function CardEditor({
  sku,
  onClose,
  onChanged,
}: {
  sku: string;
  onClose: () => void;
  onChanged: () => void;
}) {
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [adjusting, setAdjusting] = useState(false);
  const [fraction, setFraction] = useState(0.54);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  // Bumped after a rebuild so the browser fetches the new crops rather than its cached ones.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    api.item(sku).then(setDetail).catch(() => setDetail(null));
  }, [sku]);

  const rebuild = async (value: number) => {
    setBusy(true);
    setNote(null);
    try {
      await api.buildCornerDetails(sku, value);
      setVersion((v) => v + 1);
      onChanged();
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (adjusting && detail) {
    return (
      <CornerAdjuster
        sku={sku}
        item={detail}
        initialSide="front"
        onDone={() => {
          setAdjusting(false);
          onChanged();
        }}
        onCancel={() => setAdjusting(false)}
      />
    );
  }

  return (
    <div className="card-editor">
      <div className="editor-row">
        <div>
          <strong>Where the card&apos;s edges are</strong>
          <span className="muted">
            {" "}
            — fixes a crop that clipped the card or took in the table.
          </span>
        </div>
        <button disabled={!detail} onClick={() => setAdjusting(true)}>
          Drag the four corners
        </button>
      </div>

      <div className="editor-row">
        <div>
          <strong>How close the corner shots sit</strong>
          <span className="muted"> — only affects the four close-ups.</span>
        </div>
        <label className="editor-slider">
          <input
            type="range"
            min={15}
            max={90}
            value={Math.round(fraction * 100)}
            disabled={busy}
            onChange={(e) => setFraction(Number(e.target.value) / 100)}
            onMouseUp={() => rebuild(fraction)}
            onTouchEnd={() => rebuild(fraction)}
          />
          <span className="muted">{Math.round(fraction * 100)}% of the card</span>
        </label>
      </div>

      <div className="editor-preview">
        {["tl", "tr", "bl", "br"].map((corner) => (
          <img
            key={corner}
            src={`/api/images/${sku}/detail-front-${corner}.jpg?w=400&v=${version}`}
            alt={`corner ${corner}`}
          />
        ))}
      </div>

      {note ? <p className="error">{note}</p> : null}
      <button className="linklike" onClick={onClose}>
        done editing
      </button>
    </div>
  );
}
