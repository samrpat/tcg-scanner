import { useCallback, useEffect, useRef, useState } from "react";
import { api, type InventoryRow } from "./api";
import { useCamera } from "./useCamera";
import { isTyping } from "./keys";
import Hint from "./Hint";

/**
 * The extra pass.
 *
 * A separate screen because it is a separate physical job. Scanning is place-shoot-flip-shoot,
 * two thousand times, and it stays that fast by never asking a question. Angled shots are the
 * opposite: most cards do not want one and a handful badly do, so somebody has to look at each
 * card and decide — which is a second pass over the pile, not an interruption of the first.
 *
 * One card at a time, its scan on screen so you know which card to pick up, and two ways out:
 * shoot it or skip it. Skip is the common answer, so skip is the cheapest key.
 */

/** Ignores repeat presses inside this window — a double-tap costs a wasted photograph. */
const DEBOUNCE_MS = 600;

type Batch = Awaited<ReturnType<typeof api.sessions>>["sessions"][number];

export default function Extras() {
  const lastShotAt = useRef(0);
  const { videoRef, ready, error: cameraError, grabFrame } = useCamera(true);

  const [batches, setBatches] = useState<Batch[]>([]);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [cards, setCards] = useState<InventoryRow[]>([]);
  const [index, setIndex] = useState(0);
  // Cards that already have one are usually cards you dealt with on a previous pass, so the
  // default is to walk only what is left. Turning it off is how you go back and add a second.
  const [onlyMissing, setOnlyMissing] = useState(true);
  const [added, setAdded] = useState(0);
  const [skipped, setSkipped] = useState(0);

  const [busy, setBusy] = useState(false);
  const [dataError, setDataError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);
  const error = dataError ?? cameraError;

  // --- what we are walking through ---------------------------------------------------------

  const loadBatches = useCallback(async () => {
    try {
      const list = await api.sessions();
      setBatches(list.sessions);
      setBatchId((current) => current ?? list.current_id);
    } catch (e) {
      setDataError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    loadBatches();
  }, [loadBatches]);

  const loadCards = useCallback(async () => {
    if (!batchId) return;
    try {
      const inv = await api.inventory(undefined, false, { session_id: batchId });
      setCards(inv.cards);
      setDataError(null);
    } catch (e) {
      setDataError(e instanceof Error ? e.message : String(e));
    }
  }, [batchId]);

  useEffect(() => {
    setIndex(0);
    setAdded(0);
    setSkipped(0);
    loadCards();
  }, [loadCards]);

  // The queue is computed rather than stored, so a card that just gained a shot leaves it on
  // its own when "only cards without one" is on.
  const queue = onlyMissing ? cards.filter((c) => c.extras === 0) : cards;
  const card = queue[index];
  const done = !card && cards.length > 0;

  // --- the two answers ---------------------------------------------------------------------

  const skip = useCallback(() => {
    if (!card) return;
    setSkipped((n) => n + 1);
    setStatus(null);
    setIndex((i) => i + 1);
  }, [card]);

  const capture = useCallback(
    async (advance: boolean) => {
      const now = Date.now();
      if (!card || busy || now - lastShotAt.current < DEBOUNCE_MS) return;
      lastShotAt.current = now;

      const blob = await grabFrame();
      if (!blob) {
        setStatus("No camera frame");
        return;
      }

      setBusy(true);
      navigator.vibrate?.(20);
      try {
        const result = await api.captureExtraFor(card.sku, blob, "webcam");
        // Patch the row rather than refetching the batch: at two thousand cards a reload per
        // photograph is the difference between this being quick and being a wait.
        setCards((rows) =>
          rows.map((r) => (r.sku === card.sku ? { ...r, extras: result.extras } : r)),
        );
        setAdded((n) => n + 1);
        setStatus(`${result.extras} on ${card.sku}`);
        if (advance && !onlyMissing) setIndex((i) => i + 1);
        // With the filter on, the card removes itself from the queue and the index already
        // points at the next one — advancing as well would step over a card.
      } catch (e) {
        setDataError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [card, busy, grabFrame, onlyMissing],
  );

  const back = useCallback(() => {
    setStatus(null);
    setIndex((i) => Math.max(0, i - 1));
  }, []);

  // Space and Enter shoot, so a Bluetooth remote works as a pedal here too. S and the arrows
  // skip, because skip is the answer on most cards and it should never need the screen.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e)) return;
      if (e.code === "Space" || e.code === "Enter") {
        e.preventDefault();
        capture(true);
      } else if (e.code === "KeyS" || e.code === "ArrowRight") {
        e.preventDefault();
        skip();
      } else if (e.code === "ArrowLeft") {
        e.preventDefault();
        back();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [capture, skip, back]);

  // --- screen ------------------------------------------------------------------------------

  const shown = batches.find((b) => b.id === batchId);

  return (
    <div className="extras">
      <Hint>
        A second pass for the few cards wanting a seventh photograph — a holo tilted so the
        foil reads, a crease close up. Most cards want <strong>Skip</strong>.
      </Hint>
      <div className="batch-bar">
        <div>
          <span className="field-label">Extras</span>
          <select
            value={batchId ?? ""}
            onChange={(e) => setBatchId(e.target.value || null)}
            disabled={busy}
          >
            {batches.map((b) => (
              <option key={b.id} value={b.id}>
                {b.name} ({b.cards})
              </option>
            ))}
          </select>
        </div>
        <span className="spacer" />
        <label className="chip" title="Cards you have already shot drop out of the queue">
          <input
            type="checkbox"
            checked={onlyMissing}
            onChange={(e) => {
              setOnlyMissing(e.target.checked);
              setIndex(0);
            }}
          />
          only cards without one
        </label>
        <span className="muted">
          {added} added · {skipped} skipped
        </span>
      </div>

      {error ? <p className="error">{error}</p> : null}

      {!shown || cards.length === 0 ? (
        <p className="muted">
          Nothing in this batch to go through. Scan a pile first, or pick another batch above.
        </p>
      ) : done ? (
        <div className="extras-done">
          <h2>Pass complete</h2>
          <p className="muted">
            {added} extra shot{added === 1 ? "" : "s"} added, {skipped} card
            {skipped === 1 ? "" : "s"} skipped.
          </p>
          {/* Only when the queue was empty — adding nothing because you skipped everything is
              not the same as there being nothing to do, and the two used to say the same. */}
          {onlyMissing && queue.length === 0 ? (
            <p className="muted">Every card in this batch has one.</p>
          ) : null}
          <button onClick={() => setIndex(0)}>Go through it again</button>
        </div>
      ) : (
        <div className="extras-stage">
          <figure className="extras-reference">
            <img src={`${card.thumbnail}&w=400`} alt={card.sku} />
            <figcaption>
              <strong>{card.sku}</strong>
              {card.name ? <span className="muted"> {card.name}</span> : null}
              <span className="muted">
                {index + 1} of {queue.length}
                {card.extras ? ` · ${card.extras} already` : ""}
              </span>
            </figcaption>
          </figure>

          <div className="extras-camera">
            <video ref={videoRef} autoPlay playsInline muted />
            {status ? <span className="extras-status">{status}</span> : null}
          </div>
        </div>
      )}

      {card ? (
        <div className="extras-controls">
          <button className="extras-skip" onClick={skip} disabled={busy}>
            Skip <span className="muted">(S)</span>
          </button>
          <button
            className="extras-shoot"
            onClick={() => capture(true)}
            disabled={!ready || busy || card.extras >= 3}
          >
            {card.extras >= 3 ? "Three is the limit" : "Capture extra"}{" "}
            <span className="muted">(space)</span>
          </button>
          <button className="linklike" onClick={back} disabled={index === 0 || busy}>
            back
          </button>
        </div>
      ) : null}

      <p className="note">
        Frame whatever the standard six photographs do not show — a holo tilted so the foil
        reads, a crease close up, a signature — then <strong>Capture extra</strong>. Most cards
        want <strong>Skip</strong>. <strong>Space</strong> shoots,{" "}
        <strong>S</strong> skips, <strong>←</strong> goes back, so a Bluetooth remote works here
        as well as on the scan screen.
      </p>
      <p className="note muted">
        A skip is not remembered. Cards you shot drop out of the queue and stay out, but coming
        back to this tab offers the skipped ones again — there is no reason to make &ldquo;not
        this one&rdquo; permanent, and plenty of reasons to get a second look at a pile.
      </p>
    </div>
  );
}
