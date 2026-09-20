import { useEffect, useState } from "react";
import { api } from "./api";

/**
 * The front door, and the decision about whether there is one.
 *
 * First run asks rather than assumes. A password is right for almost everybody and wrong for
 * somebody running this on a wired network in a locked room who does not want to type one a
 * hundred times a day — and that used to be an environment variable, which means a file, a
 * restart, and knowing the variable exists.
 *
 * The choice is reversible from Settings, and the answer is stored, so "no password" reads as
 * a decision rather than as an installation nobody finished. Those are different states and
 * the API treats them differently: an unclaimed instance serves nothing at all.
 */

type Screen = "choose" | "password" | "recovery" | "login" | "recover";

function deviceLabel(): string {
  const ua = navigator.userAgent;
  if (/iPhone|Android.*Mobile/.test(ua)) return "Phone";
  if (/iPad|Tablet/.test(ua)) return "Tablet";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  return "Browser";
}

export default function Gate({
  claimed,
  minLength,
  onIn,
}: {
  claimed: boolean;
  minLength: number;
  onIn: () => void;
}) {
  const [screen, setScreen] = useState<Screen>(claimed ? "login" : "choose");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [label] = useState(deviceLabel);

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const Card = ({ children }: { children: React.ReactNode }) => (
    <div className="gate">
      <div className="gate-card">
        <h1>TCG Scanner</h1>
        {children}
        {error ? <p className="error">{error}</p> : null}
      </div>
    </div>
  );

  // ── first run: is there a password or not ────────────────────────────────────────────
  if (screen === "choose") {
    return (
      <Card>
        <p className="muted">
          Setting up. One question first: should this ask for a password?
        </p>

        <button className="gate-go" onClick={() => setScreen("password")}>
          Yes — set a password
        </button>
        <p className="muted gate-foot">
          Recommended. Without one, anything that can reach this address can read your
          collection and delete cards — on a home network that is every device on it, phones
          and televisions included.
        </p>

        <button
          className="gate-alt"
          disabled={busy}
          onClick={() =>
            run(async () => {
              await api.claimInstance(null);
              onIn();
            })
          }
        >
          No — this machine is on a network I trust
        </button>
        <p className="muted gate-foot">
          Nothing will be locked. You can add a password later from Settings without losing
          anything.
        </p>
      </Card>
    );
  }

  // ── first run: choosing it ───────────────────────────────────────────────────────────
  if (screen === "password") {
    const short = password.length > 0 && password.length < minLength;
    const mismatch = confirm.length > 0 && confirm !== password;
    const ready = password.length >= minLength && confirm === password;
    return (
      <Card>
        <p className="muted">
          Choose a password. A phrase you can type on a phone beats a short one full of
          symbols.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!ready || busy) return;
            run(async () => {
              const result = await api.claimInstance(password, label);
              setPassword("");
              setConfirm("");
              // Shown once, and only here. Nothing can read it back.
              if (result.recovery_code) {
                setRecovery(result.recovery_code);
                setScreen("recovery");
              } else {
                onIn();
              }
            });
          }}
        >
          <label className="gate-field">
            <span>Password</span>
            <input
              type="password"
              autoFocus
              autoComplete="new-password"
              value={password}
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
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
          {short ? <p className="muted">At least {minLength} characters.</p> : null}
          {mismatch ? <p className="error">Those do not match.</p> : null}
          <button className="gate-go" type="submit" disabled={!ready || busy}>
            {busy ? "…" : "Set the password"}
          </button>
        </form>
        <button className="linklike" onClick={() => setScreen("choose")}>
          back
        </button>
      </Card>
    );
  }

  // ── the recovery code, shown exactly once ────────────────────────────────────────────
  if (screen === "recovery" && recovery) {
    return (
      <Card>
        <h2 className="gate-sub">Write this down</h2>
        <p className="muted">
          Your recovery code. It is the only way back in if you forget the password — there is
          no email here to send a reset to.
        </p>
        <pre className="recovery-code">{recovery}</pre>
        <p className="muted">
          It will not be shown again. A photograph of this screen is fine; a note in the same
          password manager as the password is not, because then one loss takes both.
        </p>
        <button className="gate-go" onClick={onIn}>
          I have saved it
        </button>
      </Card>
    );
  }

  // ── recovering ───────────────────────────────────────────────────────────────────────
  if (screen === "recover") {
    const ready = code.trim().length >= 20 && password.length >= minLength;
    return (
      <Card>
        <h2 className="gate-sub">Use a recovery code</h2>
        <p className="muted">
          The code you saved when this was set up. Every signed-in device will be signed out.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (!ready || busy) return;
            run(async () => {
              const result = await api.recoverWithCode(code, password);
              setCode("");
              setPassword("");
              setRecovery(result.recovery_code);
              setScreen("recovery");
            });
          }}
        >
          <label className="gate-field">
            <span>Recovery code</span>
            <input
              autoFocus
              autoCapitalize="characters"
              spellCheck={false}
              placeholder="ABCDE-FGHJK-LMNPQ-RSTUV-WXYZ2"
              value={code}
              disabled={busy}
              onChange={(e) => setCode(e.target.value)}
            />
          </label>
          <label className="gate-field">
            <span>New password</span>
            <input
              type="password"
              autoComplete="new-password"
              value={password}
              disabled={busy}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button className="gate-go" type="submit" disabled={!ready || busy}>
            {busy ? "…" : "Set a new password"}
          </button>
        </form>
        <p className="muted gate-foot">
          No code? Whoever runs the machine can reset it there with{" "}
          <code>make set-password</code>.
        </p>
        <button className="linklike" onClick={() => setScreen("login")}>
          back
        </button>
      </Card>
    );
  }

  // ── ordinary login ───────────────────────────────────────────────────────────────────
  return (
    <Card>
      <p className="muted">Enter the password to unlock this scanner.</p>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (password.length === 0 || busy) return;
          run(async () => {
            await api.login(password, label);
            setPassword("");
            onIn();
          });
        }}
      >
        <label className="gate-field">
          <span>Password</span>
          <input
            type="password"
            autoFocus
            autoComplete="current-password"
            value={password}
            disabled={busy}
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        <button className="gate-go" type="submit" disabled={!password || busy}>
          {busy ? "…" : "Unlock"}
        </button>
      </form>
      <p className="muted gate-foot">
        Signed in for 30 days on this device.{" "}
        <button className="linklike" onClick={() => setScreen("recover")}>
          Forgotten it?
        </button>
      </p>
    </Card>
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

    // Coalesced. A session expiring does not produce one 401, it produces one per request in
    // flight — and a screen mid-poll has several. Re-checking once per burst turns that into
    // a single question; without it the answer to "am I logged out" was asked nine times in
    // the same tick, which is a stampede pointed at the one endpoint that must always answer.
    let pending: number | undefined;
    const onOut = () => {
      if (pending !== undefined) return;
      pending = window.setTimeout(() => {
        pending = undefined;
        refresh();
      }, 250);
    };

    window.addEventListener("tcg-unauthenticated", onOut);
    return () => {
      if (pending !== undefined) clearTimeout(pending);
      window.removeEventListener("tcg-unauthenticated", onOut);
    };
  }, []);

  return { ...state, refresh };
}
