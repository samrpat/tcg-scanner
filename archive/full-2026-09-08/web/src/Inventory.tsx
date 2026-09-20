import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type InventoryRow, type LotRow } from "./api";
import CardDetail from "./CardDetail";
import ExportPanel from "./ExportPanel";
import Sessions from "./Sessions";

/**
 * The whole collection at once.
 *
 * Every other screen judges one card. This one decides what to *do* with them: sort by value,
 * see the total, and find the handful worth listing alone among the many that are not.
 *
 * The lot machinery lives here because it follows directly from what the numbers say. eBay's
 * fixed per-order fee and the postage are paid once per order, so cards that are each marginal
 * alone are comfortably worth selling together — measured on the first twelve priced cards,
 * $13.51 of stock nets $6.02 sold one by one and $11.23 as a single lot, for one listing
 * instead of twelve.
 */

const VERDICTS = [
  { key: "", label: "All" },
  { key: "list", label: "Worth listing" },
  { key: "marginal", label: "Marginal" },
  { key: "bulk", label: "Bulk" },
];

export default function Inventory() {
  const [rows, setRows] = useState<InventoryRow[]>([]);
  const [totals, setTotals] = useState<Record<string, unknown> | null>(null);
  const [lots, setLots] = useState<LotRow[]>([]);
  const [verdict, setVerdict] = useState("");
  const [unlotted, setUnlotted] = useState(false);
  const [cardSet, setCardSet] = useState("");
  const [search, setSearch] = useState("");
  const [minPrice, setMinPrice] = useState("");
  const [sets, setSets] = useState<{ name: string; count: number }[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [sort, setSort] = useState<"value" | "sku" | "name">("value");
  const [editing, setEditing] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    const [inv, ls] = await Promise.all([
      api.inventory(verdict || undefined, unlotted, {
        card_set: cardSet,
        q: search,
        min_price: minPrice,
      }),
      api.lots(),
    ]);
    setRows(inv.cards);
    setTotals(inv.totals as Record<string, unknown>);
    setLots(ls);
  }, [verdict, unlotted, cardSet, search, minPrice]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    api
      .inventorySets()
      .then((d) => setSets(d.sets))
      .catch(() => {
        /* the table already reports a dead API */
      });
  }, []);

  const sorted = useMemo(() => {
    const copy = [...rows];
    if (sort === "value") {
      // Most valuable first: the reason to open this screen is to find what is worth attention.
      copy.sort(
        (a, b) => (b.value.ebay_estimate ?? -1) - (a.value.ebay_estimate ?? -1),
      );
    } else if (sort === "name") {
      copy.sort((a, b) => (a.name ?? "").localeCompare(b.name ?? ""));
    } else {
      copy.sort((a, b) => a.sku.localeCompare(b.sku));
    }
    return copy;
  }, [rows, sort]);

  const toggle = (sku: string) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(sku)) next.delete(sku);
      else next.add(sku);
      return next;
    });

  const addToLot = async (lotId: string) => {
    if (selected.size === 0) return;
    setBusy(true);
    try {
      await api.addToLot(lotId, [...selected]);
      setSelected(new Set());
      await load();
    } finally {
      setBusy(false);
    }
  };

  const newLot = async () => {
    const name = window.prompt("Name this lot", "Bundle");
    if (!name) return;
    setBusy(true);
    try {
      const lot = await api.createLot(name);
      if (selected.size > 0) await api.addToLot(lot.id, [...selected]);
      setSelected(new Set());
      await load();
    } finally {
      setBusy(false);
    }
  };

  if (editing) {
    return (
      <CardDetail sku={editing} onClose={() => setEditing(null)} onChanged={load} />
    );
  }

  return (
    <div className="inventory">
      {totals ? (
        <div className="inv-totals">
          <div>
            <strong>{String(totals.count)}</strong>
            <span>cards</span>
          </div>
          <div>
            <strong>${Number(totals.estimate).toFixed(2)}</strong>
            <span>eBay estimate</span>
          </div>
          <div>
            <strong>${Number(totals.net_if_all_sold_separately).toFixed(2)}</strong>
            <span>net if sold one by one</span>
          </div>
          <div>
            <strong>{String(totals.priced)}</strong>
            <span>priced</span>
          </div>
        </div>
      ) : null}

      <div className="inv-controls">
        {VERDICTS.map((v) => (
          <button
            key={v.key}
            className={verdict === v.key ? "chip on" : "chip"}
            onClick={() => setVerdict(v.key)}
          >
            {v.label}
          </button>
        ))}
        <label className="chip">
          <input
            type="checkbox"
            checked={unlotted}
            onChange={(e) => setUnlotted(e.target.checked)}
          />
          not in a lot
        </label>
        <select
          className="chip"
          value={cardSet}
          onChange={(e) => setCardSet(e.target.value)}
        >
          <option value="">Every set</option>
          {sets.map((s) => (
            <option key={s.name} value={s.name}>
              {s.name} ({s.count})
            </option>
          ))}
        </select>
        <input
          className="chip inv-search"
          value={search}
          placeholder="name or number"
          onChange={(e) => setSearch(e.target.value)}
        />
        <label className="chip">
          over $
          <input
            className="inv-price"
            inputMode="decimal"
            value={minPrice}
            onChange={(e) => setMinPrice(e.target.value)}
          />
        </label>
        {cardSet || search || minPrice || verdict || unlotted ? (
          <button
            className="link-button"
            onClick={() => {
              setCardSet("");
              setSearch("");
              setMinPrice("");
              setVerdict("");
              setUnlotted(false);
            }}
          >
            clear filters
          </button>
        ) : null}
        <span className="spacer" />
        <a
          className="chip"
          href="/api/inventory/export.csv"
          title="A spreadsheet of the collection for your own records — not the eBay file"
        >
          Collection CSV
        </a>
        <label>
          sort
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="value">value</option>
            <option value="sku">scanned</option>
            <option value="name">name</option>
          </select>
        </label>
      </div>

      <Sessions onChange={load} />

      <div className="autolot">
        <strong>Bundle what is not worth listing alone</strong>
        {(["set", "mixed"] as const).map((by) => (
          <button
            key={by}
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const preview = await api.autoLots({
                  by,
                  verdict: "bulk",
                  max_per_lot: 50,
                  dry_run: true,
                });
                if (!preview.created.length) {
                  window.alert(
                    "Nothing to bundle — every bulk card is already in a lot, or the only " +
                      "ones left are alone in their set.",
                  );
                  return;
                }
                const summary = preview.created
                  .map((l) => `  ${l.cards}  ${l.name}`)
                  .join("\n");
                if (
                  window.confirm(
                    `Make these ${preview.created.length} lots from ` +
                      `${preview.would_lot} cards?\n\n${summary}\n\n` +
                      "Nothing has been changed yet. You can take any card back out afterwards.",
                  )
                ) {
                  await api.autoLots({
                    by,
                    verdict: "bulk",
                    max_per_lot: 50,
                    dry_run: false,
                  });
                  await load();
                }
              } catch (e) {
                window.alert(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            {by === "set" ? "One lot per set" : "One mixed lot"}
          </button>
        ))}
        <span className="muted">
          Bulk cards only — under $0.50 on TCGplayer. Shows you the split before anything
          changes, and a card can always be taken back out.
        </span>
      </div>

      <ExportPanel />

      {selected.size > 0 ? (
        <div className="inv-selection">
          <strong>{selected.size} selected</strong>
          <button disabled={busy} onClick={newLot}>
            New lot from selection
          </button>
          {lots.map((l) => (
            <button key={l.id} disabled={busy} onClick={() => addToLot(l.id)}>
              Add to {l.name}
            </button>
          ))}
          {/* A card can be taken back out. Without this the only way to undo a mis-added
              card was to delete the whole lot. */}
          {/* Real sold prices, for the cards worth paying to look up. Each result costs
              money, so this is never automatic and never the whole collection. */}
          <button
            className="danger"
            disabled={busy}
            onClick={async () => {
              if (
                !window.confirm(
                  `Delete ${selected.size} card${selected.size === 1 ? "" : "s"}?\n\n` +
                    "Their photographs are deleted too, and this cannot be undone.",
                )
              )
                return;
              setBusy(true);
              try {
                const r = await api.deleteCards([...selected]);
                setSelected(new Set());
                await load();
                window.alert(`Deleted ${r.deleted} cards and ${r.files_removed} image files.`);
              } catch (e) {
                window.alert(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Delete
          </button>
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              try {
                const r = await api.fetchEbaySold([...selected]);
                const ok = r.priced.filter((p) => p.price).length;
                const parts = [`${ok} of ${selected.size} priced from real sales`];
                if (r.failed.length) parts.push(`${r.failed.length} failed: ${r.failed[0].error}`);
                if (r.skipped.length) parts.push(`${r.skipped.length} skipped`);
                window.alert(parts.join("\n"));
                await load();
              } catch (e) {
                window.alert(e instanceof Error ? e.message : String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Get real eBay prices
          </button>
          {[...selected].some((sku) => rows.find((r) => r.sku === sku)?.lot) ? (
            <button
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  for (const l of lots) {
                    const mine = [...selected].filter((s) => l.skus.includes(s));
                    if (mine.length) await api.removeFromLot(l.id, mine);
                  }
                  setSelected(new Set());
                  await load();
                } finally {
                  setBusy(false);
                }
              }}
            >
              Remove from lot
            </button>
          ) : null}
          <button className="ghost" onClick={() => setSelected(new Set())}>
            clear
          </button>
        </div>
      ) : null}

      <table className="inv-table">
        <thead>
          <tr>
            <th />
            <th />
            <th>Card</th>
            <th>Variant</th>
            <th>Cond</th>
            <th className="num">eBay est</th>
            <th className="num">In hand</th>
            <th>Verdict</th>
            <th>Lot</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {sorted.map((c) => (
            <tr key={c.sku} className={selected.has(c.sku) ? "on" : ""}>
              <td>
                <input
                  type="checkbox"
                  checked={selected.has(c.sku)}
                  onChange={() => toggle(c.sku)}
                />
              </td>
              <td>
                {c.thumbnail ? (
                  <img className="inv-thumb" src={c.thumbnail} alt={c.sku} />
                ) : null}
              </td>
              <td>
                <button className="linklike" onClick={() => setEditing(c.sku)}>
                  {c.name ?? c.sku}
                </button>
                <div className="muted small">
                  {c.set ?? ""} {c.number ? `#${c.number}` : ""}
                </div>
              </td>
              <td className="muted small">{c.variant ?? "—"}</td>
              <td className="muted small">{c.condition ?? "—"}</td>
              <td className="num">
                {c.value.ebay_estimate != null
                  ? `$${c.value.ebay_estimate.toFixed(2)}`
                  : "—"}
              </td>
              <td className="num">
                {c.value.net != null ? `$${c.value.net.toFixed(2)}` : "—"}
              </td>
              <td>
                <span className={`verdict ${c.value.verdict ?? ""}`}>
                  {c.value.verdict ?? "unpriced"}
                </span>
              </td>
              <td className="muted small">{c.lot ?? "—"}</td>
              <td className="muted small">
                {c.approved ? (
                  <button
                    className="linklike"
                    title="Send it back through Approve"
                    onClick={async () => {
                      await api.unapprove(c.sku);
                      load();
                    }}
                  >
                    approved ✓
                  </button>
                ) : (
                  ""
                )}
                {/* Per-row so they are findable without first working out that actions live
                    behind a selection. */}
                <button
                  className="linklike"
                  title="Front and back were photographed the wrong way round. Swaps them and identifies the card again."
                  disabled={busy}
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Swap ${c.sku}'s front and back?\n\n` +
                          "Both photographs are kept — only their roles change. The card is " +
                          "cropped and identified again afterwards.",
                      )
                    )
                      return;
                    setBusy(true);
                    try {
                      await api.swapSides(c.sku);
                      await load();
                    } catch (e) {
                      window.alert(e instanceof Error ? e.message : String(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  swap sides
                </button>
                <button
                  className="linklike danger-link"
                  title="Delete this card and its photographs"
                  disabled={busy}
                  onClick={async () => {
                    if (
                      !window.confirm(
                        `Delete ${c.sku}${c.name ? ` (${c.name})` : ""}?\n\n` +
                          "Its photographs are deleted too. This cannot be undone.",
                      )
                    )
                      return;
                    setBusy(true);
                    try {
                      await api.deleteCards([c.sku]);
                      await load();
                    } catch (e) {
                      window.alert(e instanceof Error ? e.message : String(e));
                    } finally {
                      setBusy(false);
                    }
                  }}
                >
                  delete
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {lots.length > 0 ? (
        <section className="lots">
          <h3>Lots</h3>
          {lots.map((l) => (
            <div className="lot" key={l.id}>
              <div className="lot-head">
                <strong>{l.name}</strong>
                <span className="muted">{l.economics.cards} cards</span>
                <button
                  className="ghost"
                  onClick={async () => {
                    if (!window.confirm(`Delete "${l.name}"? Its cards are released.`)) return;
                    await api.deleteLot(l.id);
                    load();
                  }}
                >
                  delete
                </button>
              </div>
              <div className="lot-price">
                <label>
                  asking $
                  <input
                    defaultValue={l.asking_price ?? ""}
                    placeholder={l.economics.suggested_price.toFixed(2)}
                    onBlur={async (e) => {
                      const raw = e.target.value.trim();
                      const amount = raw === "" ? null : Number.parseFloat(raw);
                      if (raw !== "" && !Number.isFinite(amount as number)) return;
                      await api.setLotPrice(l.id, amount);
                      load();
                    }}
                  />
                </label>
                <span className="muted">
                  blank uses the suggested ${l.economics.suggested_price.toFixed(2)}
                </span>
              </div>
              <div className="lot-economics">
                <span>
                  suggested <strong>${l.economics.suggested_price.toFixed(2)}</strong>
                </span>
                <span>
                  net as a lot <strong>${l.economics.net.toFixed(2)}</strong>
                </span>
                <span className="muted">
                  vs ${l.economics.net_if_sold_separately.toFixed(2)} sold one by one
                </span>
                {l.economics.advantage > 0 ? (
                  <span className="advantage">
                    +${l.economics.advantage.toFixed(2)} by bundling
                  </span>
                ) : null}
              </div>
            </div>
          ))}
        </section>
      ) : null}
    </div>
  );
}
