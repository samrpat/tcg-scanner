import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/**
 * Condition, stated directly.
 *
 * There is a points rubric behind this (imperfections, severities, arithmetic) and it is still
 * the more defensible route to a grade — but an operator grading their own collection already
 * knows what "Lightly Played" means and does not need walking to it through eleven checkboxes.
 * A slow path is one that gets skipped or rubber-stamped, and a rubber-stamped grade is worse
 * than a considered one-click grade.
 *
 * Nothing is pre-selected and nothing is assumed. Condition can be set here, changed here, or
 * left for listing time.
 */

const CONDITIONS: { code: string; label: string; hint: string }[] = [
  { code: "NM", label: "Near Mint", hint: "no visible wear" },
  { code: "LP", label: "Lightly Played", hint: "minor edge or surface wear" },
  { code: "MP", label: "Moderately Played", hint: "clear wear, scratches, whitening" },
  { code: "HP", label: "Heavily Played", hint: "major wear, creasing" },
  { code: "DMG", label: "Damaged", hint: "tears, water, missing material" },
];

export default function Condition({
  sku,
  onChanged,
}: {
  sku: string;
  onChanged?: () => void;
}) {
  const [current, setCurrent] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      const a = await api.assessment(sku);
      setCurrent(a.condition);
    } catch {
      setCurrent(null);
    }
  }, [sku]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="condition-picker">
      {CONDITIONS.map((c) => (
        <button
          key={c.code}
          className={current === c.code ? `cond on ${c.code}` : "cond"}
          disabled={busy}
          title={c.hint}
          onClick={async () => {
            setBusy(true);
            try {
              await api.setCondition(sku, c.code);
              setCurrent(c.code);
              onChanged?.();
            } finally {
              setBusy(false);
            }
          }}
        >
          <strong>{c.code}</strong>
          <span>{c.label}</span>
        </button>
      ))}
      {current ? (
        <button
          className="cond clear"
          disabled={busy}
          onClick={async () => {
            setBusy(true);
            try {
              await api.clearCondition(sku);
              setCurrent(null);
              onChanged?.();
            } finally {
              setBusy(false);
            }
          }}
        >
          clear
        </button>
      ) : null}
    </div>
  );
}
