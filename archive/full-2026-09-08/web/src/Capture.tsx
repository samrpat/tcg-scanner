import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ItemDetail, type Pending, type RecentItem } from "./api";
import CornerAdjuster from "./CornerAdjuster";
import CardDetail from "./CardDetail";

type Status = { tone: "idle" | "busy" | "ok" | "bad"; text: string };

/** Does this card look like it needs a human? Drives only the button's colour. */
function needsAttention(item: RecentItem): boolean {
  if (item.errors.length > 0) return true;
  return (["front", "back"] as const).some((face) => {
    const side = item[face];
    if (!side.captured) return false;
    if (!side.processed) return false;
    if (side.verdict && side.verdict !== "ok") return true;
    return side.confidence !== null && side.confidence < 0.6;
  });
}

/**
 * The capture loop is the throughput-critical screen: 100 cards/hour is 36 s/card, and every
 * pointer movement costs a second or two. So the whole cycle is driven from the space bar and
 * nothing here ever waits on the processing queue.
 */
export default function Capture() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Kept in a ref as well as state: the keyboard handler must read the current side without
  // being torn down and re-bound on every capture.
  const sideRef = useRef<"front" | "back">("front");

  const [side, setSide] = useState<"front" | "back">("front");
  const [pending, setPending] = useState<Pending | null>(null);
  const [recent, setRecent] = useState<RecentItem[]>([]);
  const [status, setStatus] = useState<Status>({ tone: "idle", text: "Ready" });
  const [cameraError, setCameraError] = useState<string | null>(null);
  // Uploads still in flight. The shutter never waits on them; this is only so the operator can
  // see that work is outstanding.
  const [inFlight, setInFlight] = useState(0);
  const [resolution, setResolution] = useState<{ w: number; h: number } | null>(null);
  const [devices, setDevices] = useState<MediaDeviceInfo[]>([]);
  const [deviceId, setDeviceId] = useState<string | undefined>(undefined);
  const [adjusting, setAdjusting] = useState<{ sku: string; side: string; item: ItemDetail } | null>(
    null,
  );
  // Which card's images are open for a closer look. The strip shows thumbnails; a listing
  // photograph is judged full size or not at all.
  const [viewing, setViewing] = useState<RecentItem | null>(null);
  // The detail view loads everything it needs itself, so opening is just a state change.
  const openViewer = useCallback((item: RecentItem) => {
    setViewing(item);
  }, []);
  const [tlsPort, setTlsPort] = useState(8443);
  const [needsGesture, setNeedsGesture] = useState(false);

  // A phone and a laptop run the identical capture path — same preview, same guide, same
  // shutter, same pipeline. The only thing that differs is that a browser will not hand out
  // the camera at all unless the page is a "secure context": HTTPS, or localhost. So the
  // laptop works over plain HTTP and the phone does not, and no permission prompt will fix
  // it. Detect that precisely rather than reporting it as a camera failure.
  const secureContext =
    typeof window !== "undefined" && window.isSecureContext && !!navigator.mediaDevices;

  const setNextSide = useCallback((next: "front" | "back") => {
    sideRef.current = next;
    setSide(next);
  }, []);

  const refresh = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([api.pending(), api.recent()]);
      setPending(p);
      setRecent(r);
      setNextSide(p.next_side);
    } catch {
      /* the dashboard reports connectivity; do not spam the capture screen */
    }
  }, [setNextSide]);

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 3000);
    return () => clearInterval(timer);
  }, [refresh]);

  useEffect(() => {
    api
      .health()
      .then((h) => setTlsPort(h.tls_port))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!secureContext) {
      setCameraError(null);
      return;
    }
    let cancelled = false;
    let active: MediaStream | null = null;

    // Ask for the sensor's full resolution. `ideal` rather than `exact` so a webcam that
    // cannot manage 4K still starts, at whatever it can do — and whatever that turns out to
    // be is displayed, because an operator cannot judge a capture they cannot measure.
    const constraints: MediaStreamConstraints = {
      video: deviceId
        ? { deviceId: { exact: deviceId }, width: { ideal: 4096 }, height: { ideal: 4096 } }
        : {
            facingMode: { ideal: "environment" },
            width: { ideal: 4096 },
            height: { ideal: 4096 },
          },
    };

    navigator.mediaDevices
      ?.getUserMedia(constraints)
      .then(async (stream) => {
        if (cancelled) {
          stream.getTracks().forEach((t) => t.stop());
          return;
        }
        active = stream;
        streamRef.current = stream;
        if (videoRef.current) {
          videoRef.current.srcObject = stream;
          // iOS Safari sometimes refuses to start playback without a gesture even for a
          // muted inline stream. Surface a button rather than showing a frozen black box.
          videoRef.current.play().catch(() => setNeedsGesture(true));
        }

        const track = stream.getVideoTracks()[0];
        // Some cameras only reveal their maximum through capabilities; push for it.
        try {
          const caps = track.getCapabilities?.();
          if (caps?.width?.max && caps?.height?.max) {
            await track.applyConstraints({
              width: { ideal: caps.width.max },
              height: { ideal: caps.height.max },
            });
          }
        } catch {
          /* the initial constraints stand */
        }

        const settings = track.getSettings();
        if (settings.width && settings.height) {
          setResolution({ w: settings.width, h: settings.height });
        }

        // Device labels are only populated after permission is granted.
        const all = await navigator.mediaDevices.enumerateDevices();
        setDevices(all.filter((d) => d.kind === "videoinput"));
      })
      .catch((e) => setCameraError(e instanceof Error ? e.message : String(e)));

    return () => {
      cancelled = true;
      active?.getTracks().forEach((t) => t.stop());
    };
  }, [deviceId, secureContext]);

  const grabFrame = useCallback(async (): Promise<Blob | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;

    // Full sensor resolution, not the size the preview happens to be laid out at.
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    setResolution({ w: video.videoWidth, h: video.videoHeight });
    // 0.98: the rectified image is derived from this, so capture artefacts propagate
    // straight into surface assessment.
    return new Promise((resolve) => canvas.toBlob((b) => resolve(b), "image/jpeg", 0.98));
  }, []);

  const shoot = useCallback(async () => {
    // The shutter waits for the frame grab and nothing else.
    //
    // Uploading a 12MP photo takes a moment and processing takes longer still, but neither has
    // any bearing on whether the operator can pick up the next card. Blocking on them turned a
    // two-second physical action into a five-second one, which at two thousand cards is over an
    // hour of standing still. The upload is fired and forgotten; failures surface in the strip.
    const shooting = sideRef.current;
    const blob = await grabFrame();
    if (!blob) {
      setStatus({ tone: "bad", text: "no camera frame available" });
      return;
    }

    // Flip immediately — the operator is already turning the card over.
    setNextSide(shooting === "front" ? "back" : "front");
    setInFlight((n) => n + 1);
    setStatus({ tone: "busy", text: `Uploading ${shooting}…` });

    api
      .captureSide(blob, shooting, "webcam")
      .then((result) => {
        setStatus({
          tone: "ok",
          text: `${result.sku} ${shooting} saved — shoot the ${result.next_side}`,
        });
        refresh();
      })
      .catch((e) => {
        // The card is still in hand, so say which side to re-shoot rather than silently
        // dropping it.
        setStatus({
          tone: "bad",
          text: `${shooting} failed to upload: ${
            e instanceof Error ? e.message : String(e)
          } — re-shoot this side`,
        });
      })
      .finally(() => setInFlight((n) => Math.max(0, n - 1)));
  }, [grabFrame, refresh, setNextSide]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (target && ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName)) return;

      if (event.code === "Space" || event.code === "Enter") {
        event.preventDefault();
        shoot();
      } else if (event.key === "f" || event.key === "F") {
        setNextSide("front");
      } else if (event.key === "b" || event.key === "B") {
        setNextSide("back");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shoot, setNextSide]);

  const nativeCapture = async (file: File | undefined) => {
    if (!file) return;
    const shooting = sideRef.current;
    setStatus({ tone: "busy", text: `Uploading ${shooting}…` });
    try {
      const result = await api.captureFile(file, shooting, "upload");
      setStatus({
        tone: "ok",
        text: `${result.sku} ${shooting} saved — shoot the ${result.next_side}`,
      });
      setNextSide(result.next_side);
      refresh();
    } catch (e) {
      setStatus({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
  };

  const upload = async (files: FileList | null) => {
    if (!files?.length) return;
    setStatus({ tone: "busy", text: `Uploading ${files.length} file(s)…` });
    try {
      const result = await api.captureBatch(Array.from(files));
      setStatus({ tone: "ok", text: `${result.accepted} image(s) accepted` });
      refresh();
    } catch (e) {
      setStatus({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
    if (fileRef.current) fileRef.current.value = "";
  };

  const recognise = async (sku: string) => {
    setStatus({ tone: "busy", text: `Identifying ${sku}…` });
    try {
      const result = await api.recognise(sku);
      setStatus({
        tone: result.needs_review ? "bad" : "ok",
        text: result.name
          ? `${sku} is ${result.name}${result.needs_review ? " — needs review" : ""}`
          : `${sku} could not be identified`,
      });
      refresh();
    } catch (e) {
      setStatus({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
  };

  const openAdjuster = async (sku: string, side: string) => {
    try {
      const item = await api.item(sku);
      setAdjusting({ sku, side, item });
    } catch (e) {
      setStatus({ tone: "bad", text: e instanceof Error ? e.message : String(e) });
    }
  };

  // If the card fills the guide, roughly what scale does the capture achieve? Below about
  // 8 px/mm a 2.5mm² defect is unmeasurable, so this is the number that decides whether the
  // camera is good enough to grade from at all.
  const guidePxPerMm = resolution
    ? Math.round(((Math.min(resolution.w, resolution.h) * 0.84) / 88) * 10) / 10
    : null;

  if (adjusting) {
    return (
      <CornerAdjuster
        sku={adjusting.sku}
        item={adjusting.item}
        initialSide={adjusting.side}
        onDone={() => {
          setAdjusting(null);
          setStatus({ tone: "ok", text: `${adjusting.sku} rectified` });
          refresh();
        }}
        onCancel={() => setAdjusting(null)}
      />
    );
  }

  // Clicking a recent capture opens the same editor the review queue uses, rather than a
  // read-only lightbox that could only show the problem and not fix it.
  if (viewing) {
    return (
      <CardDetail
        sku={viewing.sku}
        onClose={() => setViewing(null)}
        onChanged={refresh}
      />
    );
  }

  return (
    <>
      <div className="grid capture-grid">
        <div className="card stage">
          <h2>Camera</h2>
          {!secureContext ? (
            <div className="camera-fallback">
              <p>
                <strong>The camera needs HTTPS on this device.</strong>
              </p>
              <p className="note">
                Browsers only allow camera access from a secure page. Your laptop works over
                plain HTTP because <code>localhost</code> counts as secure; a phone on the
                network does not. Open this address on the phone instead and accept the
                certificate warning once:
              </p>
              <p className="https-url">
                <a href={`https://${window.location.hostname}:${tlsPort}`}>
                  https://{window.location.hostname}:{tlsPort}
                </a>
              </p>
              <p className="note">
                Everything then behaves exactly as it does here — same preview, same guide,
                same shutter. If the certificate has not been generated yet, run{" "}
                <code>make cert</code> and restart.
              </p>
            </div>
          ) : cameraError ? (
            <div className="camera-fallback">
              <p>No camera: {cameraError}</p>
              <p className="note">Upload files below instead.</p>
            </div>
          ) : (
            <div
              className="video-wrap"
              /* The preview must show exactly what gets captured. A fixed 4:3 box with
                 object-fit: cover crops a 9:16 phone stream, so the guide the operator lines
                 the card up against is not where the card actually lands in the file. */
              style={resolution ? { aspectRatio: `${resolution.w} / ${resolution.h}` } : undefined}
            >
              <video ref={videoRef} autoPlay playsInline muted />
              <div className="card-guide" aria-hidden="true" />
              {needsGesture && (
                <button
                  className="start-camera"
                  onClick={() => {
                    videoRef.current?.play();
                    setNeedsGesture(false);
                  }}
                >
                  Tap to start the camera
                </button>
              )}
            </div>
          )}

          <div className={`side-prompt ${side}`}>
            Shoot the <strong>{side.toUpperCase()}</strong>
          </div>

          <div className="capture-meta">
            <span>
              {resolution ? `${resolution.w} x ${resolution.h}` : "resolution unknown"}
              {guidePxPerMm !== null && (
                <em className={guidePxPerMm < 8 ? "bad" : guidePxPerMm < 15 ? "warn" : "ok"}>
                  {" "}
                  ~{guidePxPerMm} px/mm if the card fills the guide
                </em>
              )}
            </span>
            {devices.length > 1 && (
              <select
                value={deviceId ?? ""}
                onChange={(e) => setDeviceId(e.target.value || undefined)}
              >
                <option value="">Default camera</option>
                {devices.map((d, i) => (
                  <option key={d.deviceId} value={d.deviceId}>
                    {d.label || `Camera ${i + 1}`}
                  </option>
                ))}
              </select>
            )}
          </div>

          <div className="actions">
            <button
              className="shutter"
              onClick={shoot}
              disabled={!!cameraError || !secureContext}
            >
              Capture {side} <kbd>space</kbd>
            </button>
            <button onClick={() => setNextSide(side === "front" ? "back" : "front")}>
              Switch side <kbd>f</kbd>/<kbd>b</kbd>
            </button>
          </div>

          <div className={`status ${status.tone}`}>{status.text}</div>

          <div className="upload-row">
            <label className="filebtn">
              Upload images
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => upload(e.target.files)}
              />
            </label>

            {/* Fallback only. It opens the native camera app, which means no alignment
                guide and no live resolution readout — the operator is shooting blind and the
                framing suffers. Offered when the in-page camera is unavailable, not instead
                of it. */}
            {(!secureContext || cameraError) && (
              <label className="filebtn">
                Native camera (no guide)
                <input
                  type="file"
                  accept="image/*"
                  capture="environment"
                  onChange={(e) => nativeCapture(e.target.files?.[0])}
                />
              </label>
            )}
          </div>
        </div>

        <div className="card">
          <h2>Session</h2>
          <div className="row">
            <span>next</span>
            <span>{pending?.next_side ?? "—"}</span>
          </div>
          <div className="row">
            <span>awaiting back</span>
            <span>{pending?.awaiting_back ?? "none"}</span>
          </div>
          <div className="row">
            <span>cards</span>
            <span>{pending?.items ?? "—"}</span>
          </div>
          <div className="row">
            <span>uploading</span>
            <span>{inFlight > 0 ? `${inFlight} in flight` : "—"}</span>
          </div>
          <div className="row">
            <span>image reviews</span>
            <span>{pending?.open_image_reviews ?? "—"}</span>
          </div>
          <p className="note" style={{ marginTop: 12 }}>
            The shutter waits only for the frame. Uploading and processing happen behind you —
            keep shooting.
          </p>
        </div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h2>Recent captures</h2>
        {recent.length === 0 ? (
          <span className="note">Nothing captured yet.</span>
        ) : (
          <div className="strip">
            {recent.map((item) => (
              <div className="thumb" key={item.sku}>
                {/* Always the same three slots so the strip does not reflow as renders land,
                    and so a gap says which one is missing rather than silently closing up. */}
                <div className="thumb-set">
                  {(
                    [
                      ["listing front", item.listing_front, item.front],
                      ["listing back", item.listing_back, item.back],
                      ["processed", item.processed_front_url, item.front],
                    ] as const
                  ).map(([label, url, side]) => (
                    <figure key={label}>
                      {url ? (
                        <img
                          src={url}
                          alt={`${item.sku} ${label}`}
                          className="clickable"
                          title="Open full size"
                          onClick={() => openViewer(item)}
                        />
                      ) : (
                        <div
                          className={`slot-placeholder ${
                            !side.captured
                              ? "uncaptured"
                              : side.error
                                ? "failed"
                                : "pending"
                          }`}
                          onClick={() => openViewer(item)}
                        >
                          <span>
                            {!side.captured
                              ? "not shot"
                              : side.error
                                ? "failed"
                                : "processing…"}
                          </span>
                        </div>
                      )}
                      <figcaption>{label}</figcaption>
                    </figure>
                  ))}
                </div>
                <div className="thumb-name">
                  {item.card ? (
                    <>
                      <strong>{item.card}</strong>
                      {item.card_number && <em> #{item.card_number}</em>}
                      {item.variant ? (
                        <span className="variant-tag">{item.variant}</span>
                      ) : (
                        <span className="variant-tag pending">variant?</span>
                      )}
                    </>
                  ) : (
                    <span className="unidentified">identifying…</span>
                  )}
                </div>
                <div className="thumb-meta">
                  <strong>{item.sku}</strong>
                  <span className={`tag ${item.verdict ?? ""}`}>
                    {item.verdict ?? (item.processed_front ? "—" : "queued")}
                  </span>
                </div>
                <div className="thumb-sides">
                  {(["front", "back"] as const).map((face) => {
                    const state = item[face];
                    const label = face === "front" ? "F" : "B";
                    const cls = state.processed ? "on" : state.captured ? "half" : "";
                    return (
                      <span
                        key={face}
                        className={`${cls} ${state.manual ? "manual" : ""}`}
                        title={
                          state.manual
                            ? `${face}: corners set by hand`
                            : state.processed
                              ? `${face}: detected automatically`
                              : state.captured
                                ? `${face}: not rectified`
                                : `${face}: not captured`
                        }
                      >
                        {label}
                        {state.manual ? "✋" : ""}
                      </span>
                    );
                  })}
                  {item.confidence !== null && <em>{(item.confidence * 100).toFixed(0)}%</em>}
                </div>
                {item.errors.length > 0 && (
                  <div className="thumb-error">{item.errors[0]}</div>
                )}
                {/* Always offered. A crop can be visibly wrong while every score says fine —
                    the detector cannot know it framed the mat instead of the card — so the
                    operator must never have to earn the right to fix it. The button turns red
                    only to draw the eye to cards that already look suspect. */}
                {!item.card && item.processed_front && (
                  <button className="fixbtn" onClick={() => recognise(item.sku)}>
                    Identify
                  </button>
                )}
                <button
                  className={`fixbtn${needsAttention(item) ? " needed" : ""}`}
                  onClick={() =>
                    openAdjuster(
                      item.sku,
                      !item.front.processed && item.front.captured ? "front" : "back",
                    )
                  }
                >
                  {item.front.manual || item.back.manual ? "Adjust corners" : "Set corners"}
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </>
  );
}
