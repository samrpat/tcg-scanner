import { useCallback, useEffect, useState } from "react";
import { api, type ApprovalQueue } from "./api";
import CornerAdjuster from "./CornerAdjuster";
import Condition from "./Condition";

/**
 * Confirm one card, then the next — and fix anything wrong without leaving.
 *
 * Every card passes through here, however confident the pipeline was. Confidence decides what to
 * *ask about*, not what is true: reprints share artwork, foil cannot be read from a photograph,
 * and a crop can be subtly wrong in ways that only matter once a buyer is looking at it.
 *
 * Everything is editable in place. An earlier version put corrections behind an "edit" button,
 * which is the same mistake as putting them on another screen: the operator is looking at the
 * card *now*, with the discrepancy in front of them, and any step between noticing and fixing is
 * a step at which they shrug and approve it anyway. The identity search, the variant picker and
 * the grading chips are all simply present.
 */

type Zoom = { label: string; x: number; y: number; w: number; h: number };

/** A search of eBay's *sold* listings for this exact card.
 *
 *  Sold prices live behind eBay's access-restricted Marketplace Insights API, but the same data
 *  is an ordinary web search a person can read. So the app does not fetch it — it takes the
 *  operator straight to it and takes the number back when they return.
 *
 *  The query matters more than it looks. A first attempt used the bare collector number
 *  ("Frogadier 021 Chaos Rising") and the results were nearly all "Choose Your Card" bulk lots,
 *  whose "$1.34 to $4.05" is a range across an entire set and says nothing about this card.
 *
 *  Two things fix it:
 *  - the **full printed number** in quotes ("021/086"). Every genuine single-card listing puts
 *    it in the title; the bare number matches every lot in the set.
 *  - **excluding the lot vocabulary** — choose, pick, lot, bundle, playset, singles — which is
 *    how those listings all describe themselves.
 *
 *  The finish is included because a reverse holo and a normal are different cards to a buyer and
 *  routinely differ severalfold in price. */
function soldListingsUrl(
  name: string,
  localId: string | null,
  setTotal: number | null,
  variant: string | null,
): string {
  // Match the width the card is actually printed with, which the collector number itself
  // reveals: "021" belongs to an "/086" set, "29" to a "/39" one. Hard-coding three digits
  // turned a 39-card set's "03/39" into "03/039", which matches nothing.
  //
  // padStart never truncates, so a total wider than the number is left alone: "35" with 113
  // cards correctly gives "35/113".
  const number =
    localId && setTotal
      ? `"${localId}/${String(setTotal).padStart(localId.length, "0")}"`
      : localId ?? "";
  const finish = variant && /reverse/i.test(variant)
    ? "reverse holo"
    : variant && /holo/i.test(variant)
      ? "holo"
      : "";
  const exclude = "-lot -bundle -playset -choose -pick -singles -bulk";
  const query = [name, number, finish, exclude].filter(Boolean).join(" ");
  return (
    "https://www.ebay.com/sch/i.html?_nkw=" +
    encodeURIComponent(query) +
    "&LH_Sold=1&LH_Complete=1&_sop=13"
  );
}

function PriceEditor({
  variantId,
  current,
  onSaved,
}: {
  variantId: string | null;
  current: number | null;
  onSaved: () => void;
}) {
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  // Reset whenever the card changes, so a figure typed for one card cannot be saved onto the
  // next one after the queue advances.
  useEffect(() => {
    setDraft("");
  }, [variantId]);

  if (!variantId) return null;

  const save = async () => {
    const amount = Number.parseFloat(draft);
    if (!Number.isFinite(amount) || amount < 0) return;
    setBusy(true);
    try {
      await api.setManualPrice(variantId, amount);
      setDraft("");
      onSaved();
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="price-editor">
      <span className="dollar">$</span>
      <input
        value={draft}
        inputMode="decimal"
        placeholder={current != null ? current.toFixed(2) : "0.00"}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            e.stopPropagation();
            save();
          }
        }}
        disabled={busy}
      />
      <button onClick={save} disabled={busy || draft.trim() === ""}>
        set
      </button>
    </span>
  );
}
type Payload = ApprovalQueue;

/** A magnified patch of a rectified card, cropped in CSS.
 *
 *  The card is rectified to exactly 88x63 mm, so the collector number and set symbol sit at
 *  fixed fractions of it. Taken from the processed render, which is the card alone — the listing
 *  render adds a 5 mm margin that would shift every fraction. */
function ZoomPatch({ url, region }: { url: string; region: Zoom }) {
  return (
    <figure className="zoom-patch">
      <div
        className="zoom-window"
        style={{
          backgroundImage: `url(${url})`,
          backgroundSize: `${100 / region.w}% ${100 / region.h}%`,
          backgroundPosition: `${(region.x / (1 - region.w)) * 100}% ${
            (region.y / (1 - region.h)) * 100
          }%`,
        }}
      />
      <figcaption>{region.label}</figcaption>
    </figure>
  );
}

function IdentityEditor({
  sku,
  current,
  onChanged,
}: {
  sku: string;
  current: Payload["card"] extends null ? never : NonNullable<Payload["card"]>["identified"];
  onChanged: () => void;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<
    { tcgdex_id: string; name: string; number?: string; set?: { name: string } }[]
  >([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(() => {
      api
        .searchCards(query.trim())
        .then((r) => !cancelled && setResults(r))
        .catch(() => setResults([]));
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [query]);

  return (
    <div className="identity-editor">
      <input
        className="detail-search"
        value={query}
        placeholder={current ? "Wrong card? Search — e.g. charmander 49" : "Search for the card"}
        onChange={(e) => setQuery(e.target.value)}
      />
      {results.length > 0 ? (
        <div className="detail-results">
          {results.slice(0, 6).map((c) => (
            <button
              key={c.tcgdex_id}
              className="choice"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  await api.setCard(sku, c.tcgdex_id);
                  setQuery("");
                  setResults([]);
                  onChanged();
                } finally {
                  setBusy(false);
                }
              }}
            >
              <strong>{c.name}</strong>
              <span className="muted">
                {c.set?.name ?? ""}
                {c.number ? ` · #${c.number}` : ""} · {c.tcgdex_id}
              </span>
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function VariantEditor({
  sku,
  cardKey,
  onChanged,
}: {
  sku: string;
  /** The identified card. Variants belong to a *card*, so correcting the identity replaces the
   *  whole list — and the SKU does not change when that happens. Keying the reload on the SKU
   *  alone left the previous card's variants (or none at all, for a card that was unidentified)
   *  on screen, and since approval requires a variant the operator was stuck until they
   *  reloaded the page. */
  cardKey: string;
  onChanged: () => void;
}) {
  const [options, setOptions] = useState<{ id: string; label: string; source: string }[]>([]);
  const [addable, setAddable] = useState<string[]>([]);
  const [catalogue, setCatalogue] = useState<string>("known");
  const [current, setCurrent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const v = await api.variants(sku);
      setOptions(v.variants);
      setAddable(v.addable ?? []);
      setCatalogue(v.catalogue ?? "known");
      setCurrent(v.current);
    } catch {
      setOptions([]);
      setAddable([]);
    }
  }, [sku, cardKey]);

  useEffect(() => {
    load();
  }, [load]);

  if (options.length === 0) {
    return (
      <span className="muted">
        Identify the card first — variants belong to a card.
      </span>
    );
  }
  return (
    <div>
      {/* A quarter of the catalogue's variant rows are synthesised rather than known, and they
          all default to "normal". Where that is all we have, say so — otherwise a holo-only
          promo silently presents "Normal" as fact. */}
      {catalogue === "generated" ? (
        <p className="variant-warning">
          The catalogue has no real printing data for this card — its list is generated and
          defaults to Normal. Trust the card in your hand.
        </p>
      ) : null}
      <div className="variant-picker inline">
      {options.map((v) => (
        <button
          key={v.id}
          className={v.id === current ? "variant-opt on" : "variant-opt"}
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.setVariant(sku, v.id);
              setCurrent(v.id);
              onChanged();
            } finally {
              setBusy(false);
            }
          }}
        >
          {v.label}
          {v.source === "operator" ? <span className="added">added</span> : null}
        </button>
      ))}
      {/* The catalogue is wrong often enough — promos especially — that a card can otherwise be
          unfinishable: approval needs a variant, and the only listed one may be plainly false.
          TCGdex reports XY48 Meowstic as normal-only when it exists only as a holo. */}
      {addable.map((f) => (
        <button
          key={f}
          className="variant-opt add"
          disabled={busy}
          title="Not in the catalogue — record it anyway"
          onClick={async () => {
            setBusy(true);
            try {
              await api.setVariantFinish(sku, f);
              await load();
              onChanged();
            } finally {
              setBusy(false);
            }
          }}
        >
          + {f === "reverse" ? "Reverse Holo" : f === "holo" ? "Holo" : "Normal"}
        </button>
      ))}
      </div>
    </div>
  );
}

export default function Approve() {
  const [data, setData] = useState<Payload | null>(null);
  const [busy, setBusy] = useState(false);
  const [adjusting, setAdjusting] = useState<"front" | "back" | null>(null);
  const [detail, setDetail] = useState<Awaited<ReturnType<typeof api.item>> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const next = await api.approvalQueue();
      setData(next);
      setError(null);
      if (next.card) setDetail(await api.item(next.card.sku));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const card = data?.card ?? null;

  const approve = useCallback(async () => {
    if (!card || busy) return;
    setBusy(true);
    try {
      await api.approve(card.sku);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [card, busy, load]);

  const setAside = useCallback(async () => {
    if (!card || busy) return;
    setBusy(true);
    try {
      await api.setAside(card.sku);
      await load();
    } finally {
      setBusy(false);
    }
  }, [card, busy, load]);

  const needsRescan = useCallback(async () => {
    if (!card || busy) return;
    setBusy(true);
    try {
      await api.approvalNeedsRescan(card.sku);
      await load();
    } finally {
      setBusy(false);
    }
  }, [card, busy, load]);

  // Enter confirms — this runs once per card across a whole collection. Suppressed while a text
  // field has focus, so typing a card name cannot approve the card by accident.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || adjusting) return;
      if (e.key === "Enter") {
        e.preventDefault();
        approve();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [approve, adjusting]);

  if (adjusting && detail && card) {
    return (
      <CornerAdjuster
        sku={card.sku}
        initialSide={adjusting}
        item={detail}
        onDone={() => {
          setAdjusting(null);
          load();
        }}
        onCancel={() => setAdjusting(null)}
      />
    );
  }

  if (!data) return <p className="note">Loading…</p>;
  if (!card) {
    return (
      <div className="card">
        <h2>Everything is approved</h2>
        <p className="note">
          {data.approved} card{data.approved === 1 ? "" : "s"} confirmed.
          {data.set_aside > 0
            ? ` ${data.set_aside} set aside — bring them back when you are ready.`
            : ""}
          {data.awaiting_rescan > 0
            ? ` ${data.awaiting_rescan} waiting to be photographed again.`
            : ""}
        </p>
        {data.set_aside > 0 ? (
          <button
            onClick={async () => {
              await api.restoreSetAside();
              load();
            }}
          >
            Bring back {data.set_aside} set aside
          </button>
        ) : null}
      </div>
    );
  }

  const listing = card.images.filter((i) => i.label.startsWith("Listing"));
  const processed = card.images.filter((i) => i.label.startsWith("Processed"));
  const zoomSource = card.images.find((i) => i.label === "Processed front");

  return (
    <div className="approve">
      <header className="approve-head">
        <div>
          <span className="sku">{card.sku}</span>
          <h2>
            {card.identified ? card.identified.name : "Not identified"}
            {card.identified?.local_id ? (
              <span className="muted"> · #{card.identified.local_id}</span>
            ) : null}
          </h2>
          <span className="muted">
            {card.identified?.set ?? "—"}
            {card.identified ? ` · ${card.identified.tcgdex_id}` : ""} · confidence{" "}
            {card.confidence.toFixed(2)}
          </span>
        </div>
        <div className="approve-progress">
          <strong>{data.remaining}</strong> left · {data.approved} approved
          {data.set_aside > 0 ? (
            <div className="parked">
              {data.set_aside} set aside
              <button
                className="linklike"
                onClick={async () => {
                  await api.restoreSetAside();
                  load();
                }}
              >
                bring back
              </button>
            </div>
          ) : null}
          {data.awaiting_rescan > 0 ? (
            <div className="parked">{data.awaiting_rescan} awaiting rescan</div>
          ) : null}
        </div>
      </header>

      {error ? <p className="error">{error}</p> : null}
      {/* Whatever the pipeline was unsure about, stated at full size before the images. With
          the separate review queue gone this is the only place these are ever seen, so they are
          a banner rather than a footnote. */}
      {card.flags.length > 0 ? (
        <div className="approve-flags">
          <h3>Needs a look</h3>
          <ul>
            {card.flags.map((f, i) => (
              <li key={i}>
                <strong>{f.category}</strong> {f.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="approve-body">
        <div className="approve-listing">
          {listing.map((slot) => (
            <figure key={slot.label}>
              {slot.url ? (
                <img src={slot.url} alt={`${card.sku} ${slot.label}`} />
              ) : (
                <div className={`slot-placeholder ${slot.state}`}>
                  <span>{slot.note ?? slot.state}</span>
                </div>
              )}
              <figcaption>
                {slot.label}
                <button
                  className="linklike inline-edit"
                  onClick={() =>
                    setAdjusting(slot.label.endsWith("back") ? "back" : "front")
                  }
                >
                  adjust corners
                </button>
              </figcaption>
            </figure>
          ))}
        </div>

        <aside className="approve-side">
          <section>
            <h3>Number and set symbol</h3>
            <p className="zoom-hint">
              One of these carries the collector number and set symbol — which corner depends on
              the era. Check it against the name above.
            </p>
            <div className="zoom-row">
              {zoomSource?.url ? (
                data.zoom.map((region) => (
                  <ZoomPatch
                    key={region.label}
                    url={zoomSource.url as string}
                    region={region}
                  />
                ))
              ) : (
                <span className="muted">No processed front yet.</span>
              )}
            </div>
          </section>

          <section>
            <h3>Card</h3>
            <IdentityEditor
              sku={card.sku}
              current={card.identified}
              onChanged={load}
            />
          </section>

          <section>
            <h3>Variant</h3>
            <VariantEditor
              sku={card.sku}
              cardKey={card.identified?.tcgdex_id ?? "unidentified"}
              onChanged={load}
            />
          </section>

          <section>
            <h3>Price</h3>
            {card.value.amount != null ? (
              <>
                {/* The headline is the eBay estimate, because that is the market being sold
                    into. The reference feed price is shown underneath. */}
                <div className="price-headline">
                  <strong>
                    ${(card.value.ebay_estimate ?? card.value.amount).toFixed(2)}
                  </strong>
                  <span className={card.value.stale ? "price-age stale" : "price-age"}>
                    {card.value.age_days === 0 ? "today" : `${card.value.age_days}d old`}
                    {card.value.stale ? " · refresh" : ""}
                  </span>
                </div>
                <p className="price-breakdown">
                  {card.value.source} ${card.value.amount.toFixed(2)}
                  {card.value.condition && card.value.multiplier != null
                    ? ` × ${card.value.condition} ${card.value.multiplier.toFixed(2)}`
                    : " · no condition set"}
                  {card.value.net != null ? ` → $${card.value.net.toFixed(2)} in hand` : ""}
                </p>
                {card.value.floored ? (
                  <p className="price-floored">
                    The feed price is below what a single card realistically sells for on eBay,
                    so the estimate uses eBay&apos;s floor. Check sold listings and set the real
                    figure.
                  </p>
                ) : null}
                {card.value.verdict ? (
                  <p className={`verdict ${card.value.verdict}`}>
                    {card.value.verdict === "list"
                      ? "Worth listing on its own."
                      : card.value.verdict === "marginal"
                        ? "Marginal — better in a bundle or lot."
                        : `Bulk — under $${card.value.break_even?.toFixed(2)} a solo listing loses money.`}
                  </p>
                ) : null}
              </>
            ) : (
              <p className="muted">
                {card.variant ? "No price yet." : "Pick a variant to price it."}
              </p>
            )}
            <div className="price-actions">
              <PriceEditor
                variantId={card.variant_id}
                current={card.value.amount}
                onSaved={load}
              />
              {card.identified ? (
                <a
                  className="sold-link"
                  href={soldListingsUrl(
                    card.identified.name,
                    card.identified.local_id,
                    card.identified.set_total,
                    card.variant,
                  )}
                  target="_blank"
                  rel="noreferrer"
                >
                  see sold on eBay ↗
                </a>
              ) : null}
            </div>
          </section>

          <section>
            <h3>Condition</h3>
            <p className="grade-principle">
              When unsure, grade down. Can be changed here or at listing time.
            </p>
            <Condition sku={card.sku} onChanged={load} />
          </section>

          <section>
            <h3>Processed</h3>
            <div className="approve-processed">
              {processed.map((slot) => (
                <figure key={slot.label}>
                  {slot.url ? (
                    <img src={slot.url} alt={`${card.sku} ${slot.label}`} />
                  ) : (
                    <div className={`slot-placeholder ${slot.state}`}>
                      <span>{slot.note ?? slot.state}</span>
                    </div>
                  )}
                  <figcaption>{slot.label.replace("Processed ", "")}</figcaption>
                </figure>
              ))}
            </div>
          </section>
        </aside>
      </div>

      <div className="approve-actions">
        {/* The common case in one press: most cards out of a collection are Near Mint, and
            requiring a grade before approval (so "approved" means finished) would otherwise add
            a second click to almost every card. */}
        {/* The primary action confirms the card. It never states a condition.
            Combining the two produced a button reading "Near Mint & confirm", which an operator
            reasonably read as confirming a grade the system had worked out — and pressing it
            twelve times created twelve Near Mint grades that nothing had measured. Asserting a
            condition has to be its own deliberate act, in the Condition panel, where the button
            says what it is claiming. */}
        <button
          className="primary"
          onClick={approve}
          disabled={busy || card.missing.length > 0}
          title={card.missing.join(", ")}
        >
          Confirm &amp; next <span className="key">Enter</span>
        </button>
        {card.missing.length > 0 ? (
          <span className="approve-missing">
            Needs: {card.missing.join(", ")}
            {card.flags.some((f) => f.category === "image") &&
            card.missing.includes("not graded")
              ? " — the capture was flagged, so grade it by eye"
              : ""}
          </span>
        ) : null}
        <span className="approve-spacer" />
        {/* A queue with no way past a hard card stops at the first one. */}
        <button onClick={setAside} disabled={busy}>
          Set aside
        </button>
        <button onClick={needsRescan} disabled={busy}>
          Needs a rescan
        </button>
      </div>
    </div>
  );
}
