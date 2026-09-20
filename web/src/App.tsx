import { useCallback, useEffect, useState } from "react";
import { api, type Health, type Job, type Rubric, type Stats } from "./api";
import { usePolling } from "./usePolling";
import { rememberTlsPort } from "./secure";
import Capture from "./Capture";
import Scan from "./Scan";
import Progress from "./Progress";
import Approve from "./Approve";
import Inventory from "./Inventory";
import Batch from "./Batch";
import Extras from "./Extras";
import Gate, { useAuthGate } from "./Gate";
import Intro from "./Intro";
import Settings from "./Settings";

/**
 * Poll an endpoint, but only while something is actually looking at the result.
 *
 * `enabled` is not a nicety. The dashboard's three feeds used to run for as long as the tab
 * was open, whichever screen was showing — the job list alone is a database query every four
 * seconds, nine hundred an hour, drawn nowhere. On a Pi that is a background load with no
 * reader, and on a laptop it is a radio that never gets to sleep.
 */
function usePoll<T>(fetcher: () => Promise<T>, ms: number, enabled = true) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!enabled) return;
    try {
      setData(await fetcher());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [fetcher, enabled]);

  useEffect(() => {
    if (!enabled) return;
    refresh();
  }, [refresh, enabled]);
  usePolling(refresh, ms, enabled);

  return { data, error, refresh };
}

function HealthCard({ health }: { health: Health | null }) {
  return (
    <div className="card">
      <h2>Services</h2>
      {health ? (
        <>
          {Object.entries(health.checks).map(([name, check]) => (
            <div className="row" key={name}>
              <span>
                <span className={`dot ${check.ok ? "ok" : "bad"}`} />
                {name}
              </span>
              <span>{check.ok ? (check.ms ? `${check.ms} ms` : "ok") : "down"}</span>
            </div>
          ))}
          <div className="row">
            <span>worker tags</span>
            <span>{health.worker_tags.join(", ") || "none"}</span>
          </div>
        </>
      ) : (
        <div className="row">
          <span>
            <span className="dot bad" />
            api
          </span>
          <span>unreachable</span>
        </div>
      )}
    </div>
  );
}

function StatsCard({ stats }: { stats: Stats | null }) {
  return (
    <div className="card">
      <h2>Catalogue</h2>
      <div className="metric">
        {stats ? stats.cards.toLocaleString() : "—"}
        <small>cards</small>
      </div>
      <div className="row">
        <span>sets</span>
        <span>{stats ? stats.sets.toLocaleString() : "—"}</span>
      </div>
      <div className="row">
        <span>variants</span>
        <span>{stats ? stats.variants.toLocaleString() : "—"}</span>
      </div>
      {stats?.cards === 0 && (
        <p className="note" style={{ marginTop: 12 }}>
          Empty. Run <code>make sync</code> or start a sync below.
        </p>
      )}
    </div>
  );
}

function ConditionsCard({ rubric }: { rubric: Rubric | null }) {
  // What the condition codes mean, not how points add up.
  //
  // This panel used to show the points rubric — NM <= 3 pts, LP <= 6 and so on — which was
  // right when a grade was derived from tallied imperfections. Condition is now stated directly
  // (D-100), so a points table describes machinery nobody drives any more, and a dashboard that
  // explains a system the app does not use is worse than one that explains nothing.
  const meanings: [string, string][] = [
    ["NM", "no visible wear"],
    ["LP", "minor edge or surface wear"],
    ["MP", "clear wear, scratches, whitening"],
    ["HP", "major wear, creasing"],
    ["DMG", "tears, water, missing material"],
  ];
  return (
    <div className="card">
      <h2>Conditions</h2>
      {meanings.map(([code, meaning]) => (
        <div className="row" key={code}>
          <span>{code}</span>
          <span>{meaning}</span>
        </div>
      ))}
      <p className="note">
        Set on each card in Approve, and changeable up to listing. Grades map outward to
        eBay&apos;s wider buckets, never overstated.
      </p>
      {rubric ? null : null}
    </div>
  );
}

function JobsCard({ jobs, onSync, onPrices }: {
  jobs: Job[] | null;
  onSync: () => void;
  onPrices: () => void;
}) {
  const busy = jobs?.some((j) => j.status === "running" || j.status === "queued") ?? false;

  return (
    <div className="card" style={{ gridColumn: "1 / -1" }}>
      <h2>Background jobs</h2>
      {jobs && jobs.length > 0 ? (
        <table>
          <thead>
            <tr>
              <th>Type</th>
              <th>Status</th>
              <th>Progress</th>
              <th>Started</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td>{job.type}</td>
                <td>
                  <span className={`tag ${job.status}`}>{job.status}</span>
                </td>
                <td>
                  {job.progress_total
                    ? `${job.progress} / ${job.progress_total}`
                    : job.error
                      ? job.error.slice(0, 60)
                      : "—"}
                </td>
                <td>{new Date(job.created_at).toLocaleTimeString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <span className="note">Nothing queued.</span>
      )}
      <div className="actions">
        <button onClick={onSync} disabled={busy}>
          Sync card data
        </button>
        <button onClick={onPrices} disabled={busy}>
          Ingest prices
        </button>
      </div>
    </div>
  );
}

type View = "dashboard" | "scan" | "capture" | "extras" | "batch" | "approve" | "inventory";

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [landed, setLanded] = useState(false);
  const gate = useAuthGate();
  const locked = gate.required && !gate.authenticated;
  const [showSettings, setShowSettings] = useState(false);
  // Null while unknown, so the introduction does not flash up for a moment on every load
  // before the answer arrives.
  const [intro, setIntro] = useState<{ done: boolean; step: number } | null>(null);

  // Whether the introduction is owed. Stored on the user rather than in this browser, so
  // dismissing it on the desktop also dismisses it on the phone.
  useEffect(() => {
    if (locked) return;
    let cancelled = false;
    api
      .settings()
      .then((s) => {
        if (!cancelled) {
          setIntro({ done: s.preferences.intro_done, step: s.preferences.intro_step });
        }
      })
      .catch(() => setIntro({ done: true, step: 0 }));
    return () => {
      cancelled = true;
    };
  }, [locked]);
  // Nothing polls behind the gate. Every one of those requests would 401, and each 401 asks
  // the shell to re-check the session — a login screen that re-renders four times a second
  // while you are trying to type into it.
  const { data: health } = usePoll(api.health, 10_000, !locked);
  // So the camera screens can name the address to open, rather than a command to run.
  useEffect(() => rememberTlsPort(health?.tls_port), [health]);
  // Scanner mode opens on the batch: the loop is shoot, check, export, next — and a status
  // page is not a step in it.
  useEffect(() => {
    if (!landed && health?.scanner_mode) {
      setView("batch");
      setLanded(true);
    }
  }, [health, landed]);
  const scanner = health?.scanner_mode ?? false;

  // The dashboard is the only reader of these, so they run only while it is on screen. In
  // scanner mode it is never on screen and the conditioning route is not even mounted.
  const dash = view === "dashboard";
  const { data: stats, refresh: refreshStats } = usePoll(api.stats, 15_000, dash);
  const { data: jobs, refresh: refreshJobs } = usePoll(api.jobs, 4_000, dash);
  const { data: rubric } = usePoll(api.rubric, 120_000, dash && !scanner);

  const start = async (fn: () => Promise<unknown>) => {
    await fn();
    refreshJobs();
    refreshStats();
  };

  if (gate.loading) return <div className="wrap" />;
  if (locked) {
    return (
      <Gate claimed={gate.claimed} minLength={gate.minLength} onIn={gate.refresh} />
    );
  }

  if (intro && !intro.done) {
    return (
      <Intro
        startAt={intro.step}
        // Remembered per step, so closing the tab half way through does not start it again
        // from the top.
        onStep={(step) => api.saveSettings({ intro_step: step }).catch(() => undefined)}
        onDone={() => {
          setIntro({ done: true, step: 0 });
          api.saveSettings({ intro_done: true }).catch(() => undefined);
        }}
      />
    );
  }

  return (
    <div className="wrap">
      {health && health.auth_required === false ? (
        <p className="auth-warning">
          <strong>No password is set on this scanner.</strong> Every screen and every
          photograph is open to anything that can reach this address. Set{" "}
          <code>AUTH_REQUIRED=true</code> in <code>.env</code> and restart.
        </p>
      ) : null}
      <header>
        <h1>TCG Scanner</h1>
        <nav className="tabs">
          {/* In scanner mode the loop is Batch, Scan, Capture. Approve, Inventory and the
              dashboard belong to identification and pricing, which are switched off — showing
              them would advertise screens that cannot do anything. */}
          {scanner ? null : (
            <button
              className={view === "dashboard" ? "active" : ""}
              onClick={() => setView("dashboard")}
            >
              Dashboard
            </button>
          )}
          <button
            className={view === "scan" ? "active" : ""}
            onClick={() => setView("scan")}
          >
            Scan
          </button>
          <button
            className={view === "capture" ? "active" : ""}
            onClick={() => setView("capture")}
          >
            Capture
          </button>
          {/* Between Capture and Batch because that is where it falls in the work: shoot the
              pile, walk it once for the holos, then check and download. */}
          <button
            className={view === "extras" ? "active" : ""}
            onClick={() => setView("extras")}
          >
            Extras
          </button>
          <button
            className={view === "batch" ? "active" : ""}
            onClick={() => setView("batch")}
          >
            Batch
          </button>
          {scanner ? null : (
            <button
              className={view === "approve" ? "active" : ""}
              onClick={() => setView("approve")}
            >
              Approve
            </button>
          )}
          {scanner ? null : (
            <button
              className={view === "inventory" ? "active" : ""}
              onClick={() => setView("inventory")}
            >
              Inventory
            </button>
          )}
        </nav>
        <span className="phase">
          {scanner ? "Scanner" : `Listing · ${health?.display_currency ?? "USD"}`}
          <button
            className="gear"
            title="Settings"
            aria-label="Settings"
            onClick={() => setShowSettings(true)}
          >
            ⚙
          </button>
        </span>
      </header>

      {view === "batch" ? (
        <Batch />
      ) : view === "scan" ? (
        <Scan />
      ) : view === "capture" ? (
        <Capture />
      ) : view === "extras" ? (
        <Extras />
      ) : view === "approve" ? (
        <Approve />
      ) : view === "inventory" ? (
        <Inventory />
      ) : (
        <div className="grid">
          <Progress />
          <HealthCard health={health} />
          <StatsCard stats={stats} />
          <ConditionsCard rubric={rubric} />
          <JobsCard
            jobs={jobs}
            onSync={() => start(() => api.startSync())}
            onPrices={() => start(() => api.startPrices())}
          />
        </div>
      )}

      {showSettings ? (
        <Settings
          onClose={() => setShowSettings(false)}
          onShowIntro={() => {
            setShowSettings(false);
            setIntro({ done: false, step: 0 });
            api.saveSettings({ intro_done: false, intro_step: 0 }).catch(() => undefined);
          }}
        />
      ) : null}

    </div>
  );
}
