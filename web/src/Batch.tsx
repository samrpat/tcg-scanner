import { useCallback, useEffect, useRef, useState } from "react";
import { api, type InventoryRow, type ItemDetail } from "./api";
import CornerAdjuster from "./CornerAdjuster";
import { usePolling } from "./usePolling";
import Hint from "./Hint";
import { useCamera } from "./useCamera";

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
  // Cards ticked for moving. A batch is a grouping and groupings get made wrong — a pile shot
  // across a break lands in two, a card belonging to yesterday's lot turns up today. Fixing
  // that should cost a tick and a dropdown, not a rescan.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const toggle = (sku: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (!next.delete(sku)) next.add(sku);
      return next;
    });
  // Which batch is on screen. Null means the one being scanned into; an id means an earlier
  // batch the operator opened. Archiving is supposed to clear the screen, and it could not
  // while this view showed the whole collection regardless of batch.
  const [viewing, setViewing] = useState<string | null>(null);
  // How the download is arranged, not what gets made — so unlike the corner switch this is a
  // preference of whoever is doing the uploading, and it lives in the browser.
  const [split, setSplit] = useState(() => {
    try {
      return localStorage.getItem("batch-split") === "on";
    } catch {
      return false;
    }
  });
  const setSplitRemembered = (on: boolean) => {
    setSplit(on);
    try {
      localStorage.setItem("batch-split", on ? "on" : "off");
    } catch {
      /* private browsing; the switch still works for this session */
    }
  };
  const [plan, setPlan] = useState<Awaited<ReturnType<typeof api.photoGroups>> | null>(null);

  // What the photo-count plan was last computed for. The plan comes from the server — the
  // same two functions the download uses, so it can never disagree with it — but it only
  // changes when the cards do, and refetching it on every six-second tick was a third of this
  // screen's traffic spent re-deriving an answer that had not moved.
  const planFor = useRef<string>("");

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

      // Everything the plan depends on: which cards, how many extra shots each carries, and
      // the batch's corner switch.
      const batch = ses.sessions.find((b) => b.id === shown);
      const signature = shown
        ? `${shown}|${batch?.corner_shots}|${inv.cards
            .map((c) => `${c.sku}:${c.extras}`)
            .join(",")}`
        : "";
      if (!shown) {
        setPlan(null);
        planFor.current = "";
      } else if (signature !== planFor.current) {
        setPlan(await api.photoGroups(shown));
        planFor.current = signature;
      }
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [viewing]);

  // A selection means nothing once a different batch is on screen.
  useEffect(() => setSelected(new Set()), [viewing]);

  useEffect(() => {
    load();
  }, [load]);
  // Cards appear as they finish processing, so this refreshes while a pile is being shot —
  // but an archived batch is finished by definition and has nothing left to appear, and a
  // background tab has nobody to show it to.
  usePolling(load, 6000, !viewing);

  const current = session?.sessions.find((s) => s.current);
  const shownBatch = viewing
    ? session?.sessions.find((s) => s.id === viewing)
    : current;
  const isArchivedView = Boolean(viewing && viewing !== current?.id);
  // Corner close-ups quadruple the file count and the upload time — worth it on a card someone
  // will zoom into, dead weight on a bulk common. The switch belongs to the *batch*, not to this
  // browser: as a local preference it only decided what went into a download, so a batch scanned
  // with it on still had no corners in it, because nothing ever cut them.
  const corners = shownBatch?.corner_shots ?? true;
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
      <Hint>
        The pile you are on. Check the crops came out, then download the folder and archive it
        to start the next.
      </Hint>
      <div className="batch-bar">
        <div>
          <span className="field-label">
            {isArchivedView ? "Viewing archived batch" : "Scanning into"}
          </span>
          {shownBatch ? (
            <BatchName
              id={shownBatch.id}
              name={shownBatch.name}
              busy={busy}
              onRenamed={load}
              onError={setError}
            />
          ) : (
            <strong>no batch yet</strong>
          )}
          <span className="muted"> · {cards.length} cards</span>
        </div>
        {isArchivedView ? (
          <button onClick={() => setViewing(null)}>back to the current batch</button>
        ) : null}
        <span className="spacer" />
        {shownBatch ? (
          <>
            {cards.length > 0 ? (
              <a
                className="primary-link"
                href={`/api/sessions/${shownBatch.id}/photos.zip${
                  split ? "?layout=count" : ""
                }`}
                download
              >
                Download all photos (.zip) ↓
              </a>
            ) : null}
            {cards.length > 0 ? (
              <label
                className="chip"
                title="A folder per photo count, so each one is a single upload"
              >
                <input
                  type="checkbox"
                  checked={split}
                  onChange={(e) => setSplitRemembered(e.target.checked)}
                />
                split by photo count
              </label>
            ) : null}

            {/* Outside the "has cards" guard on purpose: this decides what an empty batch will
                *make* as it is scanned, so it has to be settable before the first card. */}
            <label
              className="chip"
              title="Four extra images per card, for buyers who zoom in. Cut as each card is scanned."
            >
              <input
                type="checkbox"
                checked={corners}
                disabled={busy}
                onChange={(e) => {
                  const on = e.target.checked;
                  act(async () => {
                    const r = await api.setBatchCorners(shownBatch.id, on);
                    if (on && r.built.length) {
                      window.alert(
                        `Cut corner close-ups for ${r.built.length} card` +
                          `${r.built.length === 1 ? "" : "s"}.`,
                      );
                    }
                  });
                }}
              />
              corner close-ups
            </label>
            {corners && cards.length > 0 ? (
              <button
                className="linklike"
                disabled={busy}
                title="Cut corners for any card in this batch that has none"
                onClick={() =>
                  act(async () => {
                    const r = await api.setBatchCorners(shownBatch.id, true);
                    window.alert(
                      r.built.length
                        ? `Cut corner close-ups for ${r.built.length} card` +
                            `${r.built.length === 1 ? "" : "s"}.`
                        : "Every card in this batch already has them.",
                    );
                  })
                }
              >
                cut any missing
              </button>
            ) : null}
            {!corners && cards.length > 0 ? (
              <button
                className="linklike"
                disabled={busy}
                title="Delete this batch's corner images from disk"
                onClick={() => {
                  if (
                    !window.confirm(
                      `Delete the corner close-ups in "${shownBatch.name}"?\n\n` +
                        "They can be cut again at any time — nothing original is lost.",
                    )
                  )
                    return;
                  act(async () => {
                    const r = await api.deleteBatchCorners(shownBatch.id);
                    window.alert(`Removed ${r.removed} corner images.`);
                  });
                }}
              >
                delete the ones on disk
              </button>
            ) : null}
            {!isArchivedView && current && cards.length > 0 ? (
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

      {plan && plan.groups.length > 0 ? (
        <PhotoPlan plan={plan} split={split} />
      ) : null}

      {selected.size > 0 && session ? (
        <MoveBar
          count={selected.size}
          busy={busy}
          batches={session.sessions.filter((s) => s.id !== shownBatch?.id)}
          onClear={() => setSelected(new Set())}
          onMove={(destination) =>
            act(async () => {
              const skus = [...selected];
              const id =
                destination === "new"
                  ? // Born closed, so making somewhere to put cards does not quietly
                    // redirect the scanner into it mid-pile.
                    (await api.startSession(undefined, true, false)).id
                  : destination;
              const result = await api.moveCards(id, skus);
              setSelected(new Set());
              if (result.missing.length) {
                window.alert(
                  `Moved ${result.moved}. These were not found: ${result.missing.join(", ")}`,
                );
              }
            })
          }
        />
      ) : null}

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
              <label className="chip" title="Tick to move this card to another batch">
                <input
                  type="checkbox"
                  checked={selected.has(c.sku)}
                  onChange={() => toggle(c.sku)}
                />
                move
              </label>
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
                extras={c.extras}
                busy={busy}
                onChanged={load}
                onError={setError}
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
                    <td>
                      <BatchName
                        id={s.id}
                        name={s.name}
                        busy={busy}
                        onRenamed={load}
                        onError={setError}
                      />
                    </td>
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
                          href={`/api/sessions/${s.id}/photos.zip`}
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
  extras,
  busy,
  onChanged,
  onError,
  onEdit,
}: {
  sku: string;
  corners: boolean;
  extras: number;
  busy: boolean;
  onChanged: () => void;
  onError: (message: string) => void;
  onEdit: () => void;
}) {
  const shots = [
    ["listing-front", "front"],
    ["listing-back", "back"],
    // Extra shots sit with the whole-card photographs rather than with the corner crops:
    // they show the same thing — the card — under different light.
    ...Array.from({ length: extras }, (_, i): [string, string] => [
      `extra-${i + 1}`,
      `extra ${i + 1}`,
    ]),
    ...(corners
      ? ([
          ["detail-front-tl", "top left"],
          ["detail-front-tr", "top right"],
          ["detail-front-bl", "bottom left"],
          ["detail-front-br", "bottom right"],
        ] as [string, string][])
      : []),
  ] as [string, string][];

  const [shooting, setShooting] = useState(false);

  const removeExtra = async (slot: number) => {
    try {
      await api.deleteExtra(sku, slot);
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const addExtra = async (file: File) => {
    try {
      await api.captureExtraFor(sku, file);
      onChanged();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div className="batch-photos">
      {shots.map(([file, label]) => {
        const slot = file.startsWith("extra-") ? Number(file.slice(6)) : null;
        return (
          <figure key={file}>
            <img
              src={`/api/images/${sku}/${file}.jpg?w=800`}
              alt={`${sku} ${label}`}
              loading="lazy"
            />
            <figcaption className="muted">
              {label}
              {slot ? (
                <>
                  {" "}
                  <button
                    className="linklike danger-link"
                    disabled={busy}
                    onClick={() => removeExtra(slot)}
                  >
                    remove
                  </button>
                </>
              ) : null}
            </figcaption>
          </figure>
        );
      })}
      <div className="batch-photo-actions">
        <button className="linklike" onClick={onEdit}>
          edit crop &amp; corners
        </button>
        {extras < 3 ? (
          <>
            {/* Shooting one here rather than only in the Extras pass, because this is the
                screen where you notice a holo needs one — you are already looking at the card
                large, and sending someone to another tab to act on what they just saw is how a
                thing stops getting done. */}
            <button className="linklike" onClick={() => setShooting((on) => !on)}>
              {shooting ? "close the camera" : "capture an extra shot"}
            </button>
            <label className="linklike file-link" title="Upload one taken elsewhere">
              or upload one
              <input
                type="file"
                accept="image/*"
                hidden
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) addExtra(file);
                }}
              />
            </label>
          </>
        ) : (
          <span className="muted">three extra shots is the limit</span>
        )}
      </div>

      {shooting && extras < 3 ? (
        <ExtraCamera
          sku={sku}
          onCaptured={() => {
            onChanged();
            setShooting(false);
          }}
          onError={onError}
        />
      ) : null}
    </div>
  );
}

/**
 * A camera for one card, inline.
 *
 * Only mounted while it is open, which is what stops the stream: a camera left running keeps
 * the lamp on and the radio awake, and on a phone working through a pile that is a real cost
 * for a panel nobody is looking at.
 */
function ExtraCamera({
  sku,
  onCaptured,
  onError,
}: {
  sku: string;
  onCaptured: () => void;
  onError: (message: string) => void;
}) {
  const { videoRef, ready, error, grabFrame } = useCamera(true);
  const [busy, setBusy] = useState(false);

  const shoot = async () => {
    setBusy(true);
    try {
      const blob = await grabFrame();
      if (!blob) {
        onError("No camera frame yet — give it a moment.");
        return;
      }
      await api.captureExtraFor(sku, blob, "webcam");
      navigator.vibrate?.(20);
      onCaptured();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="extra-camera">
      <video ref={videoRef} autoPlay playsInline muted />
      {error ? <p className="error">{error}</p> : null}
      <button className="extras-shoot" onClick={shoot} disabled={!ready || busy}>
        {busy ? "Saving…" : `Capture extra for ${sku}`}
      </button>
      <p className="muted">Tilt the card until the foil catches the light.</p>
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

/**
 * What to do with the ticked cards.
 *
 * Appears only when something is ticked, so the screen it sits on is unchanged for the whole
 * of normal use. Moving is the one destructive-looking action here that is not destructive:
 * `session_id` is the only column that changes, no photograph is touched, and moving them
 * back is the same two clicks — so it asks for no confirmation.
 */
function MoveBar({
  count,
  busy,
  batches,
  onMove,
  onClear,
}: {
  count: number;
  busy: boolean;
  batches: { id: string; name: string; cards: number }[];
  onMove: (destination: string) => void;
  onClear: () => void;
}) {
  const [destination, setDestination] = useState("");
  return (
    <div className="move-bar">
      <strong>
        {count} card{count === 1 ? "" : "s"} ticked
      </strong>
      <span className="muted">move to</span>
      <select
        value={destination}
        disabled={busy}
        onChange={(e) => setDestination(e.target.value)}
      >
        <option value="">choose a batch…</option>
        {batches.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name} ({b.cards})
          </option>
        ))}
        <option value="new">a new batch</option>
      </select>
      <button disabled={busy || !destination} onClick={() => onMove(destination)}>
        Move
      </button>
      <button className="linklike" disabled={busy} onClick={onClear}>
        clear
      </button>
    </div>
  );
}

/**
 * A batch's name, editable in place.
 *
 * The generated name — the date and the batch number within it — is right while the pile is on
 * the bench and often wrong afterwards. "Binder A holos" is what this batch actually is, and an
 * archive list of eleven identical-looking dates is something to search rather than read.
 *
 * Click to edit, Enter to save, Escape to abandon. No dialog, because renaming a thing is not a
 * decision that deserves one — and an empty name puts the generated one back rather than leaving
 * a nameless row.
 */
function BatchName({
  id,
  name,
  busy,
  onRenamed,
  onError,
}: {
  id: string;
  name: string;
  busy: boolean;
  onRenamed: () => void;
  onError: (message: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  // Enter unmounts the input, and unmounting fires blur — which is also wired to save. Without
  // this, every rename made with the keyboard sent the request twice.
  const settled = useRef(false);

  const save = async () => {
    if (settled.current) return;
    settled.current = true;
    setEditing(false);
    if (draft.trim() === name) return;
    try {
      await api.renameSession(id, draft.trim());
      onRenamed();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  const abandon = () => {
    settled.current = true;
    setDraft(name);
    setEditing(false);
  };

  if (!editing) {
    return (
      <button
        className="batch-name"
        disabled={busy}
        title="Rename this batch"
        onClick={() => {
          setDraft(name);
          settled.current = false;
          setEditing(true);
        }}
      >
        {name}
      </button>
    );
  }

  return (
    <input
      className="batch-name-input"
      autoFocus
      value={draft}
      maxLength={120}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={save}
      onKeyDown={(e) => {
        if (e.key === "Enter") save();
        if (e.key === "Escape") abandon();
      }}
    />
  );
}

/**
 * How many photographs each card in this batch has.
 *
 * The question a bulk uploader actually asks. CardUploader and its kin are handed a flat folder
 * and told the count per card, then chunk the sorted list into groups of that size — so the
 * count has to be identical for every card in one upload. A single card carrying a seventh
 * photograph shifts every card after it by one, and the result is a listing illustrated with
 * someone else's card.
 *
 * Which is why this is on screen before the download rather than something to work out from the
 * zip afterwards: the number shown here is the number to type in.
 */
function PhotoPlan({
  plan,
  split,
}: {
  plan: NonNullable<Awaited<ReturnType<typeof api.photoGroups>>>;
  split: boolean;
}) {
  const many = plan.uploads_needed > 1;
  return (
    <div className={many && !split ? "photo-plan warn" : "photo-plan"}>
      <span className="field-label">Photos per card</span>
      {plan.groups.map((g) => (
        <span className="photo-plan-group" key={g.photos}>
          <strong>{g.photos}</strong> × {g.cards} card{g.cards === 1 ? "" : "s"}
          {split ? <code>{g.folder}/</code> : null}
        </span>
      ))}
      {many ? (
        <span className="muted">
          {split
            ? `${plan.uploads_needed} folders, one upload each.`
            : `Two counts in one folder — tick “split by photo count” or the uploader will
               group the wrong photographs together.`}
        </span>
      ) : (
        <span className="muted">One upload, {plan.groups[0].photos} photos per card.</span>
      )}
      {plan.without_photos.length ? (
        <span className="error">
          {plan.without_photos.length} card
          {plan.without_photos.length === 1 ? " has" : "s have"} no photographs and will not be
          in the download: {plan.without_photos.join(", ")}
        </span>
      ) : null}
    </div>
  );
}
