import { useCallback, useEffect, useRef, useState } from "react";
import { usePolling } from "./usePolling";
import { CAMERA_NEEDS_HTTPS, isSecureEnough, secureUrl } from "./secure";
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

/** The strip only ever shows these at thumbnail size, so ask the API for a thumbnail. Sending
 *  a 2900 px listing render over a phone's connection to draw it 54 px wide is the difference
 *  between the preview appearing as the card is turned over and appearing three cards later. */
function thumb(url: string | null | undefined): string | null {
  if (!url) return null;
  return url.includes("?") ? `${url}&w=240` : `${url}?w=240`;
}

/** One side of the last card. The empty box is deliberate: a side that has not finished
 *  processing should leave a visible gap rather than collapsing the row, so a back that never
 *  arrived is obvious at a glance instead of looking like a card with only a front. */
function SidePreview({ label, src }: { label: string; src: string | null }) {
  return (
    <figure className={src ? "scan-side-shot" : "scan-side-shot empty"}>
      {src ? <img src={src} alt={`last card ${label}`} /> : null}
      <figcaption>{label}</figcaption>
    </figure>
  );
}

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

  const secureContext = isSecureEnough();

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
  }, [refresh]);
  // Paused in a background tab; refreshed the moment it comes back.
  usePolling(refresh, 4000);

  useEffect(() => {
    if (!secureContext) {
      setError(`${CAMERA_NEEDS_HTTPS} Open ${secureUrl()} on this phone instead.`);
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

  const grabFrame = useCallback(async (): Promise<Blob | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) {
      setStatus("No camera frame");
      return null;
    }
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    const blob = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.98),
    );
    if (!blob) setStatus("Could not read the frame");
    return blob;
  }, []);

  /**
   * An extra photograph of the card just scanned, framed by hand.
   *
   * Every other photograph this system takes is deliberately flat and square, and that is the
   * treatment which flattens a holo into a matte card, evens the light across a scuff and
   * squares away a warp. This is the shot for whatever that hides — a tilt that catches the
   * foil, a crease close up, a signature — taken while the card is still on the bench.
   *
   * It does not touch the front/back rotation. Pressing it mid-card leaves the shutter still
   * asking for whichever side is outstanding, because an extra photograph is not a step in the
   * loop and making it one would cost a beat on every card that does not need it.
   */
  const shootExtra = useCallback(async () => {
    const now = Date.now();
    if (now - lastShotAt.current < DEBOUNCE_MS) return;
    lastShotAt.current = now;

    const blob = await grabFrame();
    if (!blob) return;

    setInFlight((n) => n + 1);
    navigator.vibrate?.([12, 40, 12]);
    try {
      const result = await api.captureExtra(blob);
      // The SKU is shown because "the card being worked on" is the newest one, and the only
      // way to notice a shot that landed on the wrong card is to say which card it landed on.
      setStatus(
        `Extra ${result.extras} on ${result.sku}` +
          (result.remaining ? ` · ${result.remaining} slot${result.remaining === 1 ? "" : "s"} left` : " · full"),
      );
      refresh();
    } catch (e) {
      setStatus(e instanceof Error ? e.message : String(e));
    } finally {
      setInFlight((n) => n - 1);
    }
  }, [grabFrame, refresh]);

  const shoot = useCallback(async () => {
    const now = Date.now();
    if (now - lastShotAt.current < DEBOUNCE_MS) return;
    lastShotAt.current = now;

    const shooting = sideRef.current;
    const blob = await grabFrame();
    if (!blob) return;

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
  }, [grabFrame, refresh]);

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
            <SidePreview label="F" src={thumb(last.listing_front ?? last.processed_front_url)} />
            <SidePreview label="B" src={thumb(last.listing_back ?? last.processed_back_url)} />
            {last.extras.map((a) => (
              <SidePreview key={a.slot} label="✦" src={thumb(a.url)} />
            ))}
            <div>
              {/* A photos-only batch never identifies anything, so there is no name coming and
                  saying "identifying…" would be a lie that never resolves. The two crops are
                  the answer to the only question being asked here — did that come out? */}
              {last.photos_only ? null : (
                <strong>{last.card ?? "identifying…"}</strong>
              )}
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
        <div className="scan-extras">
          <button
            className="scan-extra"
            onClick={shootExtra}
            disabled={!ready || !last}
            title="A photograph framed by hand — a holo tilted to catch the light, a flaw close up"
          >
            ✦ extra shot
            {last && last.extras.length ? <span> · {last.extras.length}</span> : null}
          </button>
          <button className="scan-lock" onClick={toggleFullscreen}>
            {fullscreen ? "Exit full screen" : "Full screen (locks the tab)"}
          </button>
        </div>
        <div className="scan-meta">
          {inFlight > 0 ? <span>{inFlight} uploading</span> : <span>&nbsp;</span>}
          {lastSku ? <span className="muted">{lastSku}</span> : null}
        </div>
      </div>
    </div>
  );
}
