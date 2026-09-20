import { useEffect, useState } from "react";
import { api } from "./api";

/**
 * The eBay bulk-upload file, made from the cards that are ready to list on their own.
 *
 * "Ready" means identified, approved, graded, priced — and **not in a lot**, because a card
 * being sold as part of a bundle must not also be listed singly. Pulling a card back out of a
 * lot in the table above is what puts it in this file; that is the override.
 *
 * The point of the file is that the drafts it creates need almost no editing on eBay: every
 * item specific is filled from the card's own catalogue entry, the condition is eBay's own
 * code, the price is the pricing run's, and the photographs are attached if this host can be
 * reached from the internet.
 */

/** Settings the CSV needs that this app cannot know, remembered in the browser.
 *
 * All of them are claims made to a buyer or values eBay validates, and every one of them is
 * wrong if guessed — so they are asked for once and kept, rather than defaulted to something
 * plausible. In particular the description only mentions photographs when an image host is
 * given, because eBay's servers fetch picture URLs themselves and cannot reach a laptop.
 */
const EXPORT_FIELDS = [
  { key: "location", label: "Item location", hint: "e.g. Needham Heights, MA" },
  { key: "postal_code", label: "Postcode", hint: "e.g. 02494" },
  { key: "shipping_profile", label: "Shipping policy name", hint: "as named in Seller Hub" },
  { key: "return_profile", label: "Return policy name", hint: "as named in Seller Hub" },
  { key: "payment_profile", label: "Payment policy name", hint: "as named in Seller Hub" },
  {
    key: "packaging",
    label: "Packaging sentence",
    hint: "Left out entirely if blank — never claim packaging you do not use",
  },
  {
    key: "image_base",
    label: "Public image address",
    hint: "e.g. https://something.trycloudflare.com — see below",
  },
] as const;

type ExportSettings = Record<string, string>;

const STORAGE_KEY = "ebay-export-settings";

function loadSettings(): ExportSettings {
  try {
    return JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export default function ExportPanel({ show = "ready" }: { show?: string }) {
  const [open, setOpen] = useState(false);
  const [settings, setSettings] = useState<ExportSettings>(loadSettings);

  const update = (key: string, value: string) => {
    const next = { ...settings, [key]: value };
    setSettings(next);
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    } catch {
      /* private browsing; the fields still work for this session */
    }
  };

  const [mode, setMode] = useState<"draft" | "add">("draft");
  const [host, setHost] = useState<Awaited<
    ReturnType<typeof api.photoHost>
  > | null>(null);
  const [counts, setCounts] = useState<{ ready: number; blocked: number } | null>(null);
  const [held, setHeld] = useState<Record<string, number>>({});
  const [photoNote, setPhotoNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      const all = await api.ebayQueue("all");
      setCounts({ ready: all.counts.ready, blocked: all.counts.blocked });
      const reasons: Record<string, number> = {};
      for (const c of all.cards)
        for (const b of c.blockers) reasons[b] = (reasons[b] ?? 0) + 1;
      setHeld(reasons);
      setHost(await api.photoHost());
    } catch {
      /* the table above already reports a dead API; no need to say it twice */
    }
  };

  const [required, setRequired] = useState<string[]>([]);

  useEffect(() => {
    refresh();
  }, []);

  // Either an address typed in settings or a tunnel this app can see running. Both mean the
  // file will carry pictures; neither means it will not, and the descriptions follow suit.
  const photos = Boolean(settings.image_base?.trim() || host?.url);

  const params = new URLSearchParams({ show, mode });
  for (const [k, v] of Object.entries(settings)) if (v.trim()) params.set(k, v.trim());
  const href = `/api/ebay/export.csv?${params}`;

  // A HEAD of the export is cheap, and tells us what eBay would reject the file for before
  // the operator has uploaded it and found out the slow way.
  useEffect(() => {
    (async () => {
      try {
        const check = await api.exportCheck(params.toString());
        setRequired(check.missing_required);
      } catch {
        /* the panel still works without this */
      }
    })();
    // `params` is rebuilt each render; its string form is what actually changes.
  }, [href]);

  return (
    <div className="export-panel">
      <div className="export-head">
        <a className="primary-link" href={href} download>
          Download eBay upload file (.csv) ↓
        </a>
        <span className="muted">
          {counts ? `${counts.ready} cards` : "…"}
          {photos ? ` · ${"photos attached"}` : " · no photos"} · Seller Hub → Reports → Upload
        </span>
        <label className="chip">
          <input
            type="checkbox"
            checked={mode === "draft"}
            onChange={(e) => setMode(e.target.checked ? "draft" : "add")}
          />
          send to Drafts
        </label>
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setPhotoNote(null);
            try {
              const r = await api.buildAllCornerDetails(show);
              const n = r.built.length;
              setPhotoNote(
                n === 0
                  ? `Nothing to do — every card over $${r.threshold} already has them.`
                  : `Cut corner close-ups for ${n} card${n === 1 ? "" : "s"}.`,
              );
              await refresh();
            } catch (e) {
              setPhotoNote(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Add corner close-ups
        </button>
        {host?.object_storage ? (
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            setPhotoNote(null);
            try {
              const r = await api.publishPhotos(show);
              setPhotoNote(
                r.failed.length
                  ? `Uploaded ${r.uploaded}, but ${r.failed.length} failed: ${r.failed[0].error}`
                  : `Uploaded ${r.uploaded} photographs for ${r.cards} cards.`,
              );
              await refresh();
            } catch (e) {
              setPhotoNote(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Publish photos
        </button>
        ) : null}
        {photos ? (
        <button
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              const r = await api.photoCheck(show);
              setPhotoNote(r.reason);
            } catch (e) {
              setPhotoNote(e instanceof Error ? e.message : String(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          Check photos
        </button>
        ) : null}
        <button className="link-button" onClick={() => setOpen(!open)}>
          {open ? "hide settings" : "settings"}
        </button>
      </div>

      {required.length ? (
        <p className="muted export-warn">
          <strong>eBay will reject these rows.</strong> Its template marks{" "}
          {required.join(", ")} as required and {required.length === 1 ? "it is" : "they are"}{" "}
          empty. Fill {required.length === 1 ? "it" : "them"} in under <em>settings</em> below.
        </p>
      ) : null}

      {photoNote ? <p className="muted export-warn">{photoNote}</p> : null}

      {counts?.blocked ? (
        <div className="muted export-warn">
          <strong>{counts.blocked} cards are not in the file.</strong> A card needs all of
          these before it can be listed on its own, and most of the ones below are missing more
          than one:
          <ul className="held-reasons">
            {Object.entries(held)
              .sort((a, b) => b[1] - a[1])
              .map(([reason, n]) => (
                <li key={reason}>
                  <strong>{n}</strong> {reason}
                </li>
              ))}
          </ul>
          A card in a lot is sold as part of that lot. To sell one on its own instead, select it
          in the table and press <em>Remove from lot</em> — it will appear in the file.
        </div>
      ) : null}

      {photos ? (
        <p className="muted export-warn">
          <span className="ok-chip">photos on</span> Every card carries its front, back and
          corner close-ups.{" "}
          {host?.url && !settings.image_base ? (
            <>
              Shared from <code>{host.url}</code> — run <code>make photos-off</code> when the
              upload is done.
            </>
          ) : null}
        </p>
      ) : (
        <div className="muted export-warn">
          <strong>No photos in this file yet.</strong> eBay fetches pictures from a web address,
          so it cannot see images on this computer. Run one command and they are attached
          automatically:
          <ol className="photo-howto-list">
            <li>
              <code>make photos-on</code> — shares only the card JPEGs, read-only, through a
              temporary address. The app itself stays private, which matters because it has no
              password on it.
            </li>
            <li>Reload this page. The file will say “photos attached”.</li>
            <li>
              Download, import, then <code>make photos-off</code>.
            </li>
          </ol>
          You can also import without photos and add them in Seller Hub afterwards — the file is
          correct either way, and the descriptions simply do not mention photographs.
        </div>
      )}

      {open ? (
        <div className="export-fields">
          {EXPORT_FIELDS.map((f) => (
            <label key={f.key}>
              <span>{f.label}</span>
              <input
                value={settings[f.key] ?? ""}
                placeholder={f.hint}
                onChange={(e) => update(f.key, e.target.value)}
              />
            </label>
          ))}
        </div>
      ) : null}
    </div>
  );
}

