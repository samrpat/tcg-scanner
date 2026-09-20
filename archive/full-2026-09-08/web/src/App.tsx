import { useCallback, useEffect, useState } from "react";
import { api, type Health, type Job, type Rubric, type Stats } from "./api";
import Capture from "./Capture";
import Scan from "./Scan";
import Progress from "./Progress";
import Approve from "./Approve";
import Inventory from "./Inventory";
import Batch from "./Batch";

function usePoll<T>(fetcher: () => Promise<T>, ms: number) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setData(await fetcher());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [fetcher]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, ms);
    return () => clearInterval(timer);
  }, [refresh, ms]);

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

type View = "dashboard" | "scan" | "capture" | "batch" | "approve" | "inventory";

export default function App() {
  const [view, setView] = useState<View>("dashboard");
  const [landed, setLanded] = useState(false);
  const { data: health } = usePoll(api.health, 10_000);
  // Scanner mode opens on the batch: the loop is shoot, check, export, next — and a status
  // page is not a step in it.
  useEffect(() => {
    if (!landed && health?.scanner_mode) {
      setView("batch");
      setLanded(true);
    }
  }, [health, landed]);
  const scanner = health?.scanner_mode ?? false;

  const { data: stats, refresh: refreshStats } = usePoll(api.stats, 15_000);
  const { data: jobs, refresh: refreshJobs } = usePoll(api.jobs, 4_000);
  const { data: rubric } = usePoll(api.rubric, 120_000);

  const start = async (fn: () => Promise<unknown>) => {
    await fn();
    refreshJobs();
    refreshStats();
  };

  return (
    <div className="wrap">
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
        </span>
      </header>

      {view === "batch" ? (
        <Batch />
      ) : view === "scan" ? (
        <Scan />
      ) : view === "capture" ? (
        <Capture />
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

      <p className="note">
        <strong>Scan</strong> is the phone screen for working through a pile — shutter only.{" "}
        <strong>Capture</strong> is the desktop workbench, with camera choice, file upload and
        corner correction. <strong>Approve</strong> is the only place a decision is ever
        needed — every card passes through it, and anything the pipeline flagged is shown
        there. <strong>Inventory</strong> is where the collection is valued, bundled into lots, and
        exported as an eBay upload file — anything not in a lot goes in it. Prices come from
        TCGplayer via TCGdex,
        which runs low against eBay on cheap cards — the sold listings are one click away on
        that screen, and the price is editable there.
      </p>
      <p className="note">
        {scanner ? (
          <>
            <strong>Scan</strong> is the phone screen for working through a pile — shutter only.{" "}
            <strong>Capture</strong> is the desktop workbench, with camera choice, file upload
            and corner correction. <strong>Batch</strong> is the pile you are on: check the
            crops, fix anything shot the wrong way round, download the folder, archive and start
            the next. Cards are not identified or priced in this mode — set{" "}
            <code>SCANNER_MODE=false</code> to bring those screens back.
          </>
        ) : (
          <>
            <strong>Scan</strong> is the phone screen for working through a pile — shutter only.{" "}
            <strong>Capture</strong> is the desktop workbench. <strong>Approve</strong> is the
            only place a decision is ever needed. <strong>Inventory</strong> is where the
            collection is valued, bundled into lots, and exported as an eBay upload file.
          </>
        )}
      </p>
    </div>
  );
}
