import { useEffect, useState } from "react";
import { api } from "./api";

/**
 * The front door.
 *
 * Three states, and which one you see is decided by the API rather than guessed here:
 *
 * - **Unclaimed** — this scanner has never had a password. Choose one. Anyone who can reach
 *   the box in the minutes before that happens could claim it, which is the standard first-run
 *   trade and much kinder than shipping a machine its owner is locked out of.
 * - **Claimed** — type the password.
 * - **Off** — `AUTH_REQUIRED=false`. The shell shows a banner instead; this screen never
 *   appears, and that combination is meant to be uncomfortable to look at.
 *
 * One field, submitted with Enter. This gets typed on a phone with a card in the other hand.
 */
export default function Gate({
  claimed,
  minLength,
  onIn,
}: {
  claimed: boolean;
  minLength: number;
  onIn: () => void;
}) {
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // A name for this device in the session list, so a lost phone is identifiable later. Guessed
  // from the user agent and never shown as fact — it is a label, not an identity.
  const [label] = useState(() => {
    const ua = navigator.userAgent;
    if (/iPhone|Android.*Mobile/.test(ua)) return "Phone";
    if (/iPad|Tablet/.test(ua)) return "Tablet";
    if (/Macintosh/.test(ua)) return "Mac";
    if (/Windows/.test(ua)) return "Windows PC";
    return "Browser";
  });

  const short = password.length > 0 && password.length < minLength;
  const mismatch = !claimed && confirm.length > 0 && confirm !== password;
  const ready = password.length >= minLength && (claimed || confirm === password);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ready || busy) return;
    setBusy(true);
    setError(null);
    try {
      if (claimed) await api.login(password, label);
      else await api.claimInstance(password, label);
      setPassword("");
      setConfirm("");
      onIn();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="gate">
      <form className="gate-card" onSubmit={submit}>
        <h1>TCG Scanner</h1>
        {claimed ? (
          <p className="muted">Enter the password to unlock this scanner.</p>
        ) : (
          <p className="muted">
            This scanner has no password yet. Choose one — it is the only thing standing between
            your collection and anything else on this network.
          </p>
        )}

        <label className="gate-field">
          <span>Password</span>
          <input
            type="password"
            autoFocus
            autoComplete={claimed ? "current-password" : "new-password"}
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>

        {!claimed ? (
          <label className="gate-field">
            <span>Again</span>
            <input
              type="password"
              autoComplete="new-password"
              value={confirm}
              disabled={busy}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </label>
        ) : null}

        {short ? (
          <p className="muted">
            At least {minLength} characters. A phrase you can type on a phone beats a short one
            full of symbols.
          </p>
        ) : null}
        {mismatch ? <p className="error">Those do not match.</p> : null}
        {error ? <p className="error">{error}</p> : null}

        <button className="gate-go" type="submit" disabled={!ready || busy}>
          {busy ? "…" : claimed ? "Unlock" : "Set the password"}
        </button>

        <p className="muted gate-foot">
          Signed in for 30 days on this device. Forgotten it?{" "}
          <code>make set-password</code> on the machine itself.
        </p>
      </form>
    </div>
  );
}

/** Whether the shell should be showing the gate, refreshed on demand and on any 401. */
export function useAuthGate() {
  const [state, setState] = useState<{
    loading: boolean;
    required: boolean;
    claimed: boolean;
    authenticated: boolean;
    minLength: number;
  }>({ loading: true, required: true, claimed: true, authenticated: false, minLength: 10 });

  const refresh = async () => {
    try {
      const status = await api.authStatus();
      setState({
        loading: false,
        required: status.required,
        claimed: status.claimed,
        authenticated: status.authenticated,
        minLength: status.min_password_length,
      });
    } catch {
      // The status endpoint is the one thing that always answers, so a failure here is the API
      // being down rather than a session problem. Treat it as "carry on" and let the screens
      // show their own errors, instead of a login box for a server that cannot check one.
      setState((s) => ({ ...s, loading: false }));
    }
  };

  useEffect(() => {
    refresh();
    const onOut = () => refresh();
    window.addEventListener("tcg-unauthenticated", onOut);
    return () => window.removeEventListener("tcg-unauthenticated", onOut);
  }, []);

  return { ...state, refresh };
}
