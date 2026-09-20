import { useCallback, useEffect, useRef, useState } from "react";
import { api, type ItemDetail } from "./api";

type Point = { x: number; y: number };

/**
 * Drag four corners onto the card and rectify from those.
 *
 * Detection will never be perfect on a hand-held photograph against a cluttered background,
 * and a card that cannot be rectified is a card that cannot be sold. This is the difference
 * between a failed capture being a dead end and being ten seconds of work.
 */
export default function CornerAdjuster({
  sku,
  item,
  initialSide,
  onDone,
  onCancel,
}: {
  sku: string;
  item: ItemDetail;
  initialSide: string;
  onDone: () => void;
  onCancel: () => void;
}) {
  // Both sides are corrected from here. A card needs its front AND its back rectified, and
  // going back to the strip between them costs a round trip for no reason.
  const [side, setSide] = useState(initialSide);
  const [corrected, setCorrected] = useState<Record<string, boolean>>({});
  const detail = item.sides[side];
  const wrapRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const [display, setDisplay] = useState({ width: 0, height: 0 });
  const [points, setPoints] = useState<Point[]>([]);
  const [dragging, setDragging] = useState<number | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [zoom, setZoom] = useState(6);
  // Which handle the magnifier follows. Set on grab and kept after release, so a corner can
  // be checked once it is placed rather than only while the mouse is held down.
  const [focused, setFocused] = useState<number | null>(null);

  const naturalWidth = detail?.width ?? 0;
  const naturalHeight = detail?.height ?? 0;
  const scale = naturalWidth ? display.width / naturalWidth : 1;

  // Start from whatever detection proposed; otherwise an inset rectangle, which is a
  // shorter drag than starting from the frame edges.
  useEffect(() => {
    if (!naturalWidth || !naturalHeight) return;
    if (detail?.corners?.length === 4) {
      setPoints(detail.corners.map(([x, y]) => ({ x, y })));
      return;
    }
    const insetX = naturalWidth * 0.2;
    const insetY = naturalHeight * 0.2;
    setPoints([
      { x: insetX, y: insetY },
      { x: naturalWidth - insetX, y: insetY },
      { x: naturalWidth - insetX, y: naturalHeight - insetY },
      { x: insetX, y: naturalHeight - insetY },
    ]);
  }, [detail?.corners, naturalWidth, naturalHeight, side]);

  const measure = useCallback(() => {
    const img = imgRef.current;
    if (img?.clientWidth) setDisplay({ width: img.clientWidth, height: img.clientHeight });
  }, []);

  useEffect(() => {
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [measure]);

  const moveTo = useCallback(
    (clientX: number, clientY: number) => {
      if (dragging === null || !wrapRef.current || !scale) return;
      const box = wrapRef.current.getBoundingClientRect();
      const x = Math.max(0, Math.min(naturalWidth, (clientX - box.left) / scale));
      const y = Math.max(0, Math.min(naturalHeight, (clientY - box.top) / scale));
      setPoints((prev) => prev.map((p, i) => (i === dragging ? { x, y } : p)));
    },
    [dragging, scale, naturalWidth, naturalHeight],
  );

  useEffect(() => {
    if (dragging === null) return;
    const onMove = (e: MouseEvent) => moveTo(e.clientX, e.clientY);
    const onTouch = (e: TouchEvent) => {
      const t = e.touches[0];
      if (t) {
        e.preventDefault();
        moveTo(t.clientX, t.clientY);
      }
    };
    const stop = () => setDragging(null);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", stop);
    window.addEventListener("touchmove", onTouch, { passive: false });
    window.addEventListener("touchend", stop);
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", stop);
      window.removeEventListener("touchmove", onTouch);
      window.removeEventListener("touchend", stop);
    };
  }, [dragging, moveTo]);

  useEffect(() => {
    if (focused === null) return;
    const onKey = (event: KeyboardEvent) => {
      const step = event.shiftKey ? 10 : 1;
      const delta: Record<string, [number, number]> = {
        ArrowLeft: [-step, 0],
        ArrowRight: [step, 0],
        ArrowUp: [0, -step],
        ArrowDown: [0, step],
      };
      const move = delta[event.key];
      if (!move) return;
      event.preventDefault();
      setPoints((prev) =>
        prev.map((p, i) =>
          i === focused
            ? {
                x: Math.max(0, Math.min(naturalWidth, p.x + move[0])),
                y: Math.max(0, Math.min(naturalHeight, p.y + move[1])),
              }
            : p,
        ),
      );
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [focused, naturalWidth, naturalHeight]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      await api.rectifyCorners(sku, side, points.map((p) => [p.x, p.y]));
      setCorrected((prev) => ({ ...prev, [side]: true }));

      // If the other side also exists and has not been corrected yet, move to it rather than
      // closing — a card is not finished until both faces are rectified.
      const other = side === "front" ? "back" : "front";
      if (item.sides[other] && !corrected[other]) {
        setSide(other);
        setFocused(null);
      } else {
        onDone();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!detail) return null;

  const polygon = points.map((p) => `${p.x * scale},${p.y * scale}`).join(" ");
  const shown = dragging ?? focused;
  const active = shown !== null ? points[shown] : null;

  // The loupe shows the ORIGINAL pixels around the handle, magnified, with a crosshair on the
  // exact coordinate. Without it you are placing a corner under your own fingertip and
  // guessing — the handle covers precisely the detail you are trying to line up with.
  const LOUPE = 168;
  // Sit on the opposite side from the corner being adjusted, so the magnifier never covers
  // the thing it is magnifying.
  const loupeOnLeft = active !== null && naturalWidth > 0 && active.x / naturalWidth > 0.5;
  const loupeStyle = active
    ? {
        backgroundImage: `url(${detail.original})`,
        backgroundSize: `${naturalWidth * zoom}px ${naturalHeight * zoom}px`,
        backgroundPosition: `${LOUPE / 2 - active.x * zoom}px ${LOUPE / 2 - active.y * zoom}px`,
      }
    : undefined;

  return (
    <div className="card adjuster">
      <h2>{sku} — drag the corners onto the card</h2>
      <div className="side-tabs">
        {(["front", "back"] as const).map((option) => {
          const present = Boolean(item.sides[option]);
          return (
            <button
              key={option}
              className={side === option ? "active" : ""}
              disabled={!present}
              onClick={() => {
                setSide(option);
                setFocused(null);
              }}
            >
              {option}
              {corrected[option] || item.sides[option]?.manual ? " ✓" : ""}
              {!present ? " (none)" : ""}
            </button>
          );
        })}
      </div>
      <div className="adjuster-stage" ref={wrapRef}>
        <img
          ref={imgRef}
          key={side}
          src={detail.original}
          alt={`${sku} ${side}`}
          onLoad={measure}
        />
        {display.width > 0 && (
          <svg width={display.width} height={display.height} className="adjuster-overlay">
            <polygon points={polygon} />
            {/* Edge guides.
                Corners are only half the job: a card is graded on its edges, and a quad whose
                four corners are each a pixel out still cuts through the border along the sides.
                Each edge is extended well past its corners as a thin line, so the operator can
                sight down it and see whether it runs parallel to the card's printed edge — the
                same trick as lining up a ruler. Far easier to judge than four points in
                isolation. */}
            {points.map((p, i) => {
              const q = points[(i + 1) % points.length];
              const x1 = p.x * scale;
              const y1 = p.y * scale;
              const x2 = q.x * scale;
              const y2 = q.y * scale;
              const dx = x2 - x1;
              const dy = y2 - y1;
              const len = Math.hypot(dx, dy) || 1;
              // Extend a third of the edge's length beyond each end.
              const ex = (dx / len) * len * 0.33;
              const ey = (dy / len) * len * 0.33;
              // Midpoint ticks: the middle of an edge is where bowing shows up first, and it is
              // the part furthest from any corner handle.
              const mx = (x1 + x2) / 2;
              const my = (y1 + y2) / 2;
              const nx = -dy / len;
              const ny = dx / len;
              return (
                <g key={`edge-${i}`} className="edge-guide">
                  <line x1={x1 - ex} y1={y1 - ey} x2={x2 + ex} y2={y2 + ey} />
                  <line
                    x1={mx - nx * 9}
                    y1={my - ny * 9}
                    x2={mx + nx * 9}
                    y2={my + ny * 9}
                    className="edge-tick"
                  />
                </g>
              );
            })}
            {points.map((p, i) => {
              const cx = p.x * scale;
              const cy = p.y * scale;
              const on = shown === i;
              return (
                <g key={i} className={on ? "handle active" : "handle"}>
                  {/* Hollow ring, so the pixel under the corner stays visible. */}
                  <circle cx={cx} cy={cy} r={14} className="ring" />
                  {/* Crosshair marking the exact coordinate, with a gap at the centre. */}
                  <line x1={cx - 20} y1={cy} x2={cx - 4} y2={cy} className="cross" />
                  <line x1={cx + 4} y1={cy} x2={cx + 20} y2={cy} className="cross" />
                  <line x1={cx} y1={cy - 20} x2={cx} y2={cy - 4} className="cross" />
                  <line x1={cx} y1={cy + 4} x2={cx} y2={cy + 20} className="cross" />
                  <circle cx={cx} cy={cy} r={1.5} className="dot" />
                  {/* Invisible, generously sized grab target — the visible ring is small on
                      purpose but a 14px touch target is not usable on a phone. */}
                  <circle
                    cx={cx}
                    cy={cy}
                    r={26}
                    className="grab"
                    onMouseDown={() => {
                      setDragging(i);
                      setFocused(i);
                    }}
                    onTouchStart={() => {
                      setDragging(i);
                      setFocused(i);
                    }}
                  />
                  <text x={cx + 20} y={cy - 20} className="handle-label">
                    {i + 1}
                  </text>
                </g>
              );
            })}
          </svg>
        )}
        {active && (
          <div className={`loupe-wrap ${loupeOnLeft ? "left" : "right"}`}>
            <div className="loupe" style={loupeStyle}>
              <div className="loupe-cross" />
            </div>
            {/* Outside the circle: inside it, the border radius clips the text. */}
            <span className="loupe-coord">
              corner {(shown ?? 0) + 1} · {Math.round(active.x)}, {Math.round(active.y)}
            </span>
          </div>
        )}
      </div>
      <div className="capture-meta">
        <span>
          {naturalWidth} x {naturalHeight} source
          {detail.confidence !== null && ` · detected ${(detail.confidence * 100).toFixed(0)}%`}
        </span>
        <label className="zoomctl">
          magnifier
          <input
            type="range"
            min={3}
            max={16}
            step={1}
            value={zoom}
            onChange={(e) => setZoom(Number(e.target.value))}
          />
          {zoom}x
        </label>
      </div>
      <p className="note">
        Order does not matter — the corners get sorted. Put them on the card's own edge, not
        outside it: the outer millimetre is where edge wear is measured. Click a corner to
        magnify it, then nudge with the arrow keys — <kbd>shift</kbd> for 10 pixels.
      </p>
      {error && <div className="status bad">{error}</div>}
      <div className="actions">
        <button onClick={save} disabled={saving || points.length !== 4}>
          {saving ? "Rectifying…" : `Rectify ${side}`}
        </button>
        <button onClick={onDone}>Done</button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
