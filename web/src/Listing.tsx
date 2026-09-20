import { useCallback, useEffect, useState } from "react";
import { api, type ListingDraft } from "./api";

/**
 * The eBay listing for one card.
 *
 * The listing is still *created on eBay*, through "Sell a similar item" on a comparable sold
 * listing. That is deliberate and it is the operator's own method: eBay carries the category and
 * the item specifics across from the template, and for trading cards those specifics — set, card
 * number, finish, rarity — are what its faceted search filters on. A listing built from a blank
 * form usually omits half of them and is correspondingly harder to find.
 *
 * So this screen does not replace that. It supplies the parts that are tedious and error-prone by
 * hand — a correctly formatted title, the right condition code, the price, a description — and
 * remembers which past listing worked, so the next copy of the same card is two clicks.
 */

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          /* clipboard blocked; the text is selectable either way */
        }
      }}
    >
      {copied ? "copied ✓" : "copy"}
    </button>
  );
}

function Copyable({ label, text, hint }: { label: string; text: string; hint?: string }) {
  return (
    <div className="copyable">
      <div className="copyable-head">
        <span>{label}</span>
        {hint ? <span className="muted">{hint}</span> : null}
        <CopyButton text={text} />
      </div>
      <pre>{text}</pre>
    </div>
  );
}

/** The title, editable.
 *
 * The generated one reproduces eBay's own catalogue name for the card, which is what attaches
 * the listing to the existing product page. But eBay's set naming drifts from TCGdex's in ways
 * no table covers completely, and the operator is the one with the real page open — so the
 * generated title is a starting point they can correct, not a fixed string.
 */
function TitleField({ generated }: { generated: string }) {
  const [text, setText] = useState(generated);
  const [touched, setTouched] = useState(false);
  useEffect(() => {
    if (!touched) setText(generated);
  }, [generated, touched]);
  return (
    <div className="copyable">
      <div className="copyable-head">
        <span>Title</span>
        <span className={text.length > 80 ? "over-limit" : "muted"}>
          {text.length}/80 characters · condition goes below, not here
        </span>
        {touched && text !== generated ? (
          <button
            onClick={() => {
              setText(generated);
              setTouched(false);
            }}
          >
            reset
          </button>
        ) : null}
        <CopyButton text={text} />
      </div>
      <textarea
        className="title-edit"
        rows={2}
        value={text}
        onChange={(e) => {
          setText(e.target.value);
          setTouched(true);
        }}
      />
    </div>
  );
}

export default function Listing({ sku }: { sku: string }) {
  const [data, setData] = useState<{
    template_item_id: string | null;
    draft: ListingDraft;
  } | null>(null);
  const [paste, setPaste] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setData(await api.listingDraft(sku));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setData(null);
    }
  }, [sku]);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <p className="muted">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;

  const d = data.draft;

  return (
    <div className="listing">
      <ol className="listing-steps">
        <li>
          <strong>Find a listing that sold.</strong>
          <a href={d.sold_search_url ?? "#"} target="_blank" rel="noreferrer">
            open sold listings ↗
          </a>
          <span className="muted">
            Pick a good one for this exact card, then copy its web address.
          </span>
        </li>
        <li>
          <strong>Save it as the template.</strong>
          <span className="listing-paste">
            <input
              value={paste}
              placeholder="Paste the eBay listing link here"
              onChange={(e) => setPaste(e.target.value)}
            />
            <button
              disabled={busy || paste.trim() === ""}
              onClick={async () => {
                setBusy(true);
                try {
                  await api.setListingTemplate(sku, paste.trim());
                  setPaste("");
                  await load();
                } catch (e) {
                  setError(e instanceof Error ? e.message : String(e));
                } finally {
                  setBusy(false);
                }
              }}
            >
              save
            </button>
          </span>
          {data.template_item_id ? (
            <span className="muted">Saved: item {data.template_item_id}</span>
          ) : (
            <span className="muted">
              eBay copies the category and card details from it — that is what makes the listing
              show up in searches.
            </span>
          )}
        </li>
        <li>
          <strong>Start the listing on eBay.</strong>
          {d.template_url ? (
            <a className="primary-link" href={d.template_url} target="_blank" rel="noreferrer">
              Sell a similar item ↗
            </a>
          ) : (
            <span className="muted">Save a template above first.</span>
          )}
        </li>
        <li>
          <strong>Paste these in.</strong>
          <span className="muted">
            Everything below is ready to copy. Condition is left out of the title on purpose —
            it belongs in eBay&apos;s Condition field, where its own search filter lives, and the
            title characters are better spent on words people actually search.
          </span>
        </li>
      </ol>

      <div className="listing-fields">
        <TitleField generated={d.title} />
        <div className="listing-pair">
          <div>
            <span className="field-label">
              Condition — put this in eBay&apos;s Condition field
            </span>
            <div className="field-value">
              {d.condition_label ?? <em className="muted">set a condition first</em>}
              {d.condition_code ? (
                <span className="muted"> · {d.condition_code}</span>
              ) : null}
            </div>
          </div>
          <div>
            <span className="field-label">Price</span>
            <div className="field-value">
              {d.price != null ? `$${d.price.toFixed(2)}` : <em className="muted">no price</em>}
            </div>
          </div>
        </div>
        <div className="specifics">
          <div className="copyable-head">
            <span>Item specifics</span>
            <span className="muted">
              fill these into eBay&apos;s form — they are what its search filters use
            </span>
          </div>
          <table>
            <tbody>
              {Object.entries(d.specifics).map(([k, v]) => (
                <tr key={k}>
                  <th>{k}</th>
                  <td>{v}</td>
                  <td className="specific-copy">
                    <CopyButton text={v} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <Copyable label="Description" text={d.description} />
      </div>
    </div>
  );
}
