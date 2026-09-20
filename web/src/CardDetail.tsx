import { useCallback, useEffect, useState } from "react";
import { api, type ImageSlot, type ItemDetail } from "./api";
import CornerAdjuster from "./CornerAdjuster";
import Condition from "./Condition";
import Listing from "./Listing";

/**
 * Everything about one card, in one place.
 *
 * Previously a card's properties were scattered by which screen happened to own them: identity
 * lived in the review queue, the variant in the capture strip, corners behind a separate editor,
 * and the listing images nowhere at all. Fixing a misread card meant knowing which screen owned
 * which field. This is the one page that owns all of it, reachable by clicking the card
 * anywhere it appears.
 */

type Props = { sku: string; onClose: () => void; onChanged?: () => void };

type Card = {
  tcgdex_id: string;
  name: string;
  local_id?: string;
  // The catalogue returns the set as a nested object, not a string.
  set?: { tcgdex_id: string; name: string };
};

export default function CardDetail({ sku, onClose, onChanged }: Props) {
  const [detail, setDetail] = useState<ItemDetail | null>(null);
  const [images, setImages] = useState<ImageSlot[]>([]);
  const [card, setCard] = useState<Card | null>(null);
  const [variants, setVariants] = useState<{ id: string; label: string }[]>([]);
  const [currentVariant, setCurrentVariant] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Card[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [adjusting, setAdjusting] = useState<"front" | "back" | null>(null);
  const [currentVariantLabel, setCurrentVariantLabel] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      // One call for everything about this card. Looking it up in /recent was wrong: that list
      // is paginated, so an older card simply was not in it and the page claimed the card was
      // unidentified when it was not.
      const [item, vs] = await Promise.all([api.item(sku), api.variants(sku)]);
      setDetail(item);
      setVariants(vs.variants);
      setCurrentVariant(vs.current);
      setImages(item.images ?? []);
      setCard(item.card ?? null);
      setCurrentVariantLabel(item.variant ?? null);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    }
  }, [sku]);

  useEffect(() => {
    load();
  }, [load]);

  const search = useCallback(async (q: string) => {
    setQuery(q);
    if (q.trim().length < 2) {
      setResults([]);
      return;
    }
    try {
      setResults(await api.searchCards(q.trim()));
    } catch {
      setResults([]);
    }
  }, []);

  const act = async (label: string, fn: () => Promise<unknown>) => {
    setBusy(label);
    setNote(null);
    try {
      await fn();
      await load();
      onChanged?.();
      setNote(`${label} done`);
    } catch (e) {
      setNote(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  if (adjusting && detail) {
    return (
      <CornerAdjuster
        sku={sku}
        initialSide={adjusting}
        item={detail}
        onDone={() => {
          setAdjusting(null);
          load();
          onChanged?.();
        }}
        onCancel={() => setAdjusting(null)}
      />
    );
  }

  return (
    <div className="card detail">
      <header className="detail-head">
        <div>
          <h2>{sku}</h2>
          <span className="muted">
            {/* Do not claim "not identified" before the first response lands: an unloaded card
                and an unidentified one are not the same thing, and the wrong one invites the
                operator to "fix" an identification that was already correct. */}
            {detail === null ? "loading…" : (card?.name ?? "not identified")}
            {card?.local_id ? ` · #${card.local_id}` : ""}
            {currentVariantLabel ? ` · ${currentVariantLabel}` : ""}
          </span>
        </div>
        <button onClick={onClose}>Done</button>
      </header>

      {note ? <p className="note">{note}</p> : null}

      <section>
        <h3>Images</h3>
        {/* All four slots, always. A slot that is simply omitted looks the same as one nobody
            asked about — the operator could not tell "still processing" from "that failed" from
            "I never shot the back". */}
        <div className="detail-images">
          {images.map((slot) => (
            <figure key={slot.label}>
              {slot.url ? (
                <img src={slot.url} alt={`${sku} ${slot.label}`} />
              ) : (
                <div className={`slot-placeholder ${slot.state}`}>
                  <span>{slot.note ?? slot.state}</span>
                </div>
              )}
              <figcaption>{slot.label}</figcaption>
            </figure>
          ))}
        </div>
      </section>

      <section>
        <h3>Which card is it?</h3>
        <input
          className="detail-search"
          value={query}
          placeholder="Search by name, e.g. Gyarados"
          onChange={(e) => search(e.target.value)}
        />
        {results.length > 0 ? (
          <div className="detail-results">
            {results.slice(0, 8).map((c) => (
              <button
                key={c.tcgdex_id}
                className="choice"
                disabled={busy !== null}
                onClick={() =>
                  act("Card set", async () => {
                    await api.setCard(sku, c.tcgdex_id);
                    setQuery("");
                    setResults([]);
                  })
                }
              >
                <strong>{c.name}</strong>
                <span className="muted">
                  {c.set?.name ?? ""}
                  {c.local_id ? ` · #${c.local_id}` : ""} · {c.tcgdex_id}
                </span>
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <section>
        <h3>Variant</h3>
        {detail === null ? (
          <p className="muted">loading…</p>
        ) : variants.length === 0 ? (
          <p className="muted">Identify the card first.</p>
        ) : (
          <div className="variant-picker">
            {variants.map((v) => (
              <button
                key={v.id}
                className={v.id === currentVariant ? "variant-opt on" : "variant-opt"}
                disabled={busy !== null}
                onClick={() => act("Variant set", () => api.setVariant(sku, v.id))}
              >
                {v.label}
              </button>
            ))}
          </div>
        )}
      </section>

      <section>
        <h3>Condition</h3>
        <Condition sku={sku} onChanged={() => { load(); onChanged?.(); }} />
      </section>

      <section>
        <h3>Crop</h3>
        <p className="muted">
          Drag the corners onto the card. The dashed guides extend each edge past its corners —
          sight along one to check it runs parallel to the printed edge, the way you would line
          up a ruler. The outer millimetre is where edge wear is graded, so the edges matter as
          much as the corners.
        </p>
        <div className="actions">
          <button onClick={() => setAdjusting("front")}>Edit front corners</button>
          <button onClick={() => setAdjusting("back")}>Edit back corners</button>
        </div>
      </section>

      <section>
        <h3>eBay listing</h3>
        <Listing sku={sku} />
      </section>

      <section>
        <h3>Re-run</h3>
        <div className="actions">
          <button disabled={busy !== null} onClick={() => act("Reprocess", () => api.reprocess(sku))}>
            Re-run the crop
          </button>
          <button
            disabled={busy !== null}
            onClick={() => act("Re-identify", () => api.recognise(sku))}
          >
            Re-identify
          </button>
        </div>
      </section>
    </div>
  );
}
