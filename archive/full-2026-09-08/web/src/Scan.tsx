import { useCallback, useEffect, useRef, useState } from "react";
import { api, type RecentItem } from "./api";

/**
 * Mobile scanning.
 *
 * This is the screen that gets used two thousand times, so it is built around one rule: the
 * only thing on it that is easy to press is the shutter. Everything else is information.
 *
 * Deliberately different from the desktop Capture tab rather than a responsive version of it.
 * Capture is a workbench — device pickers, corner adjustment, per-card diagnostics. Holding a
 * phone in one hand and a card in the other, all of that is a hazard: a mis-tap that changes
 * the camera or opens an editor mid-run costs far more than it saves. So this screen exposes
 * the shutter, an undo, and nothing else that can alter state.
 */

/** The shutter ignores repeat presses inside this window. A double-tap on a phone is a real
 *  risk and a duplicated capture costs a manual deletion later. */
const DEBOUNCE_MS = 700;

export default function Scan() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const lastShotAt = useRef(0);

  const [side, setSide] = useState<"front" | "back">("front");
  const sideRef = useRef(side);
  sideRef.current = side;

  const [ready, setReady] = useState(false);
  const [needsGesture, setNeedsGesture] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string>("");
  const [inFlight, setInFlight] = useState(0);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  // Cards completed in this sitting, so the operator can see progress without doing arithmetic
  // against the total inventory.
  const [sessionCards, setSessionCards] = useState(0);
  const [lastSku, setLastSku] = useState<string | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

  // Lock the page while scanning.
  //
  // A phone held in one hand with a card in the other produces a lot of accidental touches. A
  // stray drag scrolls the shutter off screen; an over-scroll at the top triggers pull-to-
  // refresh and reloads mid-run; a pinch zooms and nothing lines up again. None of these are
  // recoverable without stopping and looking, which is the one thing this screen is meant to
  // avoid. Fullscreen additionally takes the browser chrome away, so the tab cannot be closed
  // or switched by a mis-tap.
  useEffect(() => {
    const body = document.body;
    const previous = {
      overflow: body.style.overflow,
      overscroll: body.style.overscrollBehavior,
      touch: body.style.touchAction,
    };
    body.style.overflow = "hidden";
    body.style.overscrollBehavior = "none";
    body.style.touchAction = "manipulation";
    return () => {
      body.style.overflow = previous.overflow;
      body.style.overscrollBehavior = previous.overscroll;
      body.style.touchAction = previous.touch;
    };
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(Boolean(document.fullscreenElement));
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  const toggleFullscreen = useCallback(async () => {
    try {
      if (document.fullscreenElement) {
        await document.exitFullscreen();
      } else {
        // Must be called from a user gesture, which is why this is a button and not automatic.
        await document.documentElement.requestFullscreen?.();
      }
    } catch {
      // iOS Safari does not implement the Fullscreen API on iPhone. Scroll locking above still
      // applies, and "Add to Home Screen" gives the same result there.
      setFullscreen(false);
    }
  }, []);

  const secureContext =
    typeof window !== "undefined" &&
    (window.isSecureContext || window.location.hostname === "localhost");

  const refresh = useCallback(async () => {
    try {
      const [pending, items] = await Promise.all([api.pending(), api.recent()]);
      setSide(pending.next_side);
      setRecent(items.slice(0, 6));
    } catch {
      /* the strip is informational; a failed poll must never interrupt scanning */
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 4000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    if (!secureContext) {
      setError("A phone camera needs HTTPS. Run `make cert` and open the https:// address.");
      return;
    }
    let cancelled = false;
    let active: MediaStream | null = null;

    navigator.mediaDevices
      ?.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 4096 },
          height: { ideal: 4096 },
        },
      })
      .then(async (stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        active = stream;
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          videoRef.current.play().catch(() => setNeedsGesture(true));
        }
        const track = stream.getVideoTracks()[0];
        try {
          const caps = track.getCapabilities?.();
          if (caps?.width?.max && caps?.height?.max) {
            await track.applyConstraints({
              width: { ideal: caps.width.max },
              height: { ideal: caps.height.max },
            });
          }
        } catch {
          /* the default resolution is still usable */
        }
        setReady(true);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

    return () => {
      cancelled = true;
      active?.getTracks().forEach((t) => t.stop());
    };
  }, [secureContext]);

  const shoot = useCallback(async () => {
    const now = Date.now();
    if (now - lastShotAt.current < DEBOUNCE_MS) return;
    lastShotAt.current = now;

    const video = videoRef.current;
    if (!video || !video.videoWidth) {
      setStatus("No camera frame");
      return;
    }
    const shooting = sideRef.current;

    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.98),
    );
    if (!blob) {
      setStatus("Could not read the frame");
      return;
    }

    // Flip the prompt straight away: the card is already being turned over. The upload is fired
    // and forgotten — waiting on it would turn a two-second action into five, which across two
    // thousand cards is over an hour of standing still.
    setSide(shooting === "front" ? "back" : "front");
    setInFlight((n) => n + 1);

    // Haptic confirmation, so the shutter can be trusted without looking at the screen.
    navigator.vibrate?.(20);

    api
      .captureSide(blob, shooting, "webcam")
      .then((result) => {
        setLastSku(result.sku);
        if (shooting === "back") setSessionCards((n) => n + 1);
        setSide(result.next_side);
        setStatus("");
        refresh();
      })
      .catch((e) => setStatus(e instanceof Error ? e.message : String(e)))
      .finally(() => setInFlight((n) => n - 1));
  }, [refresh]);

  // Space and Enter fire the shutter too, so a cheap Bluetooth remote works as a foot/thumb
  // pedal. That is the difference between a comfortable run and a sore hand at card 500.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code === "Space" || e.code === "Enter") {
        e.preventDefault();
        shoot();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shoot]);

  const last = recent[0];

  return (
    <div className={fullscreen ? "scan locked" : "scan"}>
      <div className="scan-stage">
        <video ref={videoRef} autoPlay playsInline muted />
        <div className="scan-guide" aria-hidden="true" />

        <div className="scan-top">
          <span className={`scan-side ${side}`}>
            Scan the <strong>{side}</strong>
          </span>
          <span className="scan-count">
            {sessionCards} card{sessionCards === 1 ? "" : "s"} this session
          </span>
        </div>

        {last ? (
          <div className="scan-last">
            {last.thumbnail ? <img src={last.thumbnail} alt="last card" /> : null}
            <div>
              <strong>{last.card ?? "identifying…"}</strong>
              {last.variant ? <span className="variant-tag">{last.variant}</span> : null}
              <span className="muted">{last.sku}</span>
            </div>
          </div>
        ) : null}

        {needsGesture ? (
          <button
            className="scan-gesture"
            onClick={() => {
              videoRef.current?.play();
              setNeedsGesture(false);
            }}
          >
            Tap to start the camera
          </button>
        ) : null}
      </div>

      {error ? <p className="error scan-error">{error}</p> : null}
      {status ? <p className="scan-status">{status}</p> : null}

      <div className="scan-controls">
        <button
          className={`shutter ${side}`}
          onClick={shoot}
          disabled={!ready}
          aria-label={`Capture the ${side}`}
        >
          {side === "front" ? "FRONT" : "BACK"}
        </button>
        <button className="scan-lock" onClick={toggleFullscreen}>
          {fullscreen ? "Exit full screen" : "Full screen (locks the tab)"}
        </button>
        <div className="scan-meta">
          {inFlight > 0 ? <span>{inFlight} uploading</span> : <span>&nbsp;</span>}
          {lastSku ? <span className="muted">{lastSku}</span> : null}
        </div>
      </div>
    </div>
  );
}
