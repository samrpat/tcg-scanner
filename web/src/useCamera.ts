import { useCallback, useEffect, useRef, useState } from "react";
import { CAMERA_NEEDS_HTTPS, isSecureEnough, secureUrl } from "./secure";

/**
 * The rear camera, at the best resolution it will give, plus a way to grab a frame.
 *
 * Extracted because the extra pass and the per-card capture in Batch want exactly the same
 * thing and nothing more. The scan and capture screens deliberately keep their own: Scan holds
 * the shutter loop and its scroll locking, and Capture has a device picker and file upload, so
 * folding those in here would make this the union of three screens rather than the part they
 * share.
 *
 * `active` exists so the stream is only held while something is looking at it. A camera left
 * open keeps the lamp on and the radio awake, which on a phone doing a pile of cards is a
 * battery cost for nothing.
 */
export function useCamera(active: boolean) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const secure = isSecureEnough();

  useEffect(() => {
    if (!active) {
      setReady(false);
      return;
    }
    if (!secure) {
      setError(`${CAMERA_NEEDS_HTTPS} Open ${secureUrl()} instead.`);
      return;
    }

    let cancelled = false;
    let stream: MediaStream | null = null;

    navigator.mediaDevices
      ?.getUserMedia({
        video: {
          facingMode: { ideal: "environment" },
          width: { ideal: 4096 },
          height: { ideal: 4096 },
        },
      })
      .then((opened) => {
        if (cancelled) {
          opened.getTracks().forEach((t) => t.stop());
          return;
        }
        stream = opened;
        if (videoRef.current) {
          videoRef.current.srcObject = opened;
          videoRef.current.play().catch(() => undefined);
        }
        setError(null);
        setReady(true);
      })
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));

    return () => {
      cancelled = true;
      stream?.getTracks().forEach((t) => t.stop());
      setReady(false);
    };
  }, [active, secure]);

  /** The current frame as a JPEG, or null when there is nothing to grab yet. */
  const grabFrame = useCallback(async (): Promise<Blob | null> => {
    const video = videoRef.current;
    if (!video || !video.videoWidth) return null;
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d")?.drawImage(video, 0, 0);
    return new Promise<Blob | null>((resolve) =>
      canvas.toBlob((b) => resolve(b), "image/jpeg", 0.98),
    );
  }, []);

  return { videoRef, ready, error, grabFrame };
}
