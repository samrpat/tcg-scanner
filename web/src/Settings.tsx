import { useCallback, useEffect, useState } from "react";
import { api } from "./api";

/**
 * Settings, deliberately out of the way.
 *
 * Behind a gear in the header rather than a tab, because none of it is part of the loop. The
 * loop is shoot, check, export, next; anything that is not that competes with it.
 *
 * Two kinds of thing live here and they are kept visibly apart: preferences that can be
 * changed, and configuration that is reported with a note saying where it is actually set. A
 * settings screen that appears to offer a switch it cannot throw is worse than one that says
 * the switch is in a file.
 */
export default function Settings({
  onClose,
  onShowIntro,
}: {
  onClose: () => void;
  onShowIntro: () => void;
}) {
  const [data, setData] = useState<Awaited<ReturnType<typeof api.settings>> | null>(null);
  const [devices, setDevices] = useState<Awaited<ReturnType<typeof api.devices>> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  // Readable exactly once, right after it is generated. Never fetched.
  const [recovery, setRecovery] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.settings());
      setDevices(await api.devices().catch(() => null));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Escape closes it. This is a panel over the work, not a place you navigate to.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const act = async (fn: () => Promise<unknown>, message?: string) => {
    setBusy(true);
    setError(null);
    setNote(null);
    try {
      await fn();
      if (message) setNote(message);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const config = data?.configuration;

  return (
    <div className="sheet" onClick={onClose}>
      <div className="sheet-panel" onClick={(e) => e.stopPropagation()}>
        <div className="sheet-head">
          <h2>Settings</h2>
          <button className="linklike" onClick={onClose}>
            close
          </button>
        </div>

        {error ? <p className="error">{error}</p> : null}
        {note ? <p className="muted">{note}</p> : null}

        <section>
          <h3>This install</h3>
          {config ? (
            <table>
              <tbody>
                <tr>
                  <th>Version</th>
                  <td>{data?.version}</td>
                </tr>
                <tr>
                  <th>Mode</th>
                  <td>
                    {config.mode}
                    <span className="muted"> · {config.mode_source}</span>
                  </td>
                </tr>
                <tr>
                  <th>Password</th>
                  <td>
                    {config.authentication === "on" ? (
                      "required"
                    ) : (
                      <strong className="danger-text">
                        OFF — anyone on this network can open it
                      </strong>
                    )}
                    <span className="muted"> · {config.authentication_source}</span>
                  </td>
                </tr>
                <tr>
                  <th>Photo storage</th>
                  <td>{config.storage}</td>
                </tr>
                <tr>
                  <th>Render scale</th>
                  <td>{config.listing_px_per_mm} px/mm</td>
                </tr>
              </tbody>
            </table>
          ) : (
            <p className="muted">…</p>
          )}
        </section>

        <section>
          <h3>Collection</h3>
          <p className="muted">
            {data?.collection.cards ?? "—"} cards · {data?.collection.photographs ?? "—"}{" "}
            photographs · {data?.collection.batches ?? "—"} batches
          </p>
        </section>

        <section>
          <h3>Preferences</h3>
          <label className="chip">
            <input
              type="checkbox"
              disabled={busy || !data}
              checked={data?.preferences.corner_shots_default ?? true}
              onChange={(e) =>
                act(() => api.saveSettings({ corner_shots_default: e.target.checked }))
              }
            />
            new batches cut corner close-ups
          </label>
          <p className="muted">
            The switch on each batch still wins. This only decides what a new one starts as.
          </p>
          <button className="linklike" onClick={onShowIntro}>
            show the introduction again
          </button>
        </section>

        <section>
          <h3>Recovery code</h3>
          {recovery ? (
            <>
              <pre className="recovery-code">{recovery}</pre>
              <p className="muted">
                Write it down now — it will not be shown again, and it replaces any code you
                had before.
              </p>
            </>
          ) : (
            <>
              <p className="muted">
                The only way back in if the password is forgotten. Generating a new one
                invalidates the old.
              </p>
              <button
                disabled={busy}
                onClick={() =>
                  act(async () => {
                    const r = await api.regenerateRecovery();
                    setRecovery(r.recovery_code);
                  })
                }
              >
                Show me a new recovery code
              </button>
            </>
          )}
        </section>

        <section>
          <h3>Password</h3>
          <div className="settings-password">
            <input
              type="password"
              placeholder="current"
              autoComplete="current-password"
              value={current}
              disabled={busy}
              onChange={(e) => setCurrent(e.target.value)}
            />
            <input
              type="password"
              placeholder="new"
              autoComplete="new-password"
              value={next}
              disabled={busy}
              onChange={(e) => setNext(e.target.value)}
            />
            <button
              disabled={busy || !current || next.length < 10}
              onClick={() =>
                act(async () => {
                  const r = await api.changePassword(current, next);
                  setCurrent("");
                  setNext("");
                  setNote(
                    `Changed. ${r.other_devices_signed_out} other device` +
                      `${r.other_devices_signed_out === 1 ? "" : "s"} signed out.`,
                  );
                })
              }
            >
              Change
            </button>
          </div>
          <p className="muted">
            Changing it signs every other device out — that is the point of changing it. At
            least 10 characters.
          </p>
        </section>

        <section>
          <h3>Signed-in devices</h3>
          {devices?.devices.length ? (
            <table>
              <tbody>
                {devices.devices.map((d) => (
                  <tr key={d.id}>
                    <td>
                      {d.label ?? "unnamed"}
                      {d.this_one ? <span className="muted"> · this one</span> : null}
                    </td>
                    <td className="muted">
                      {d.last_seen_at
                        ? `seen ${new Date(d.last_seen_at).toLocaleString()}`
                        : "not used yet"}
                    </td>
                    <td>
                      {d.this_one ? null : (
                        <button
                          className="linklike danger-link"
                          disabled={busy}
                          onClick={() => act(() => api.revokeDevice(d.id), "Device signed out.")}
                        >
                          sign out
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="muted">Just this one.</p>
          )}
          <p className="muted">
            Lost a phone? Sign it out here, or change the password to cut off everything at
            once.
          </p>
          <button
            className="linklike danger-link"
            disabled={busy}
            onClick={async () => {
              await api.logout().catch(() => undefined);
              window.location.reload();
            }}
          >
            sign this device out
          </button>
        </section>
      </div>
    </div>
  );
}
