import { useState } from "react";

/**
 * What this is, for somebody who has never seen it.
 *
 * Shown once, after the password is set, and reachable again from Settings. It exists because
 * every screen here assumes you already know the loop — which was true while there was exactly
 * one person using it, and stops being true the moment there are two.
 *
 * Four steps and a skip. An introduction nobody can get out of is a worse first impression
 * than no introduction.
 */

type Step = { title: string; body: React.ReactNode };

const STEPS: Step[] = [
  {
    title: "This makes photographs, not listings",
    body: (
      <>
        <p>
          Point a camera at a card and you get a snapshot. What a buyer wants is the card:
          flat on, square, cropped to its true size, lit evenly, with the corners close enough
          to judge.
        </p>
        <p>
          That is all this does, and it does it the same way every time — so a hundred cards
          come out looking like a hundred cards from one seller, rather than a hundred snapshots.
        </p>
      </>
    ),
  },
  {
    title: "Shoot front, then back",
    body: (
      <>
        <p>
          <strong>Scan</strong> is the phone screen: one big shutter, nothing else easy to
          press. Shoot the front, turn the card over, shoot the back. They pair themselves —
          there is no button to press to say "this is the same card".
        </p>
        <p className="muted">
          A phone only gives a page its camera over a secure connection, so on a phone use the{" "}
          <code>https://</code> address rather than <code>http://</code>. The Scan screen shows
          you which one if you land on the wrong one. On this machine,{" "}
          <strong>Capture</strong> does the same job with a webcam or a file.
        </p>
      </>
    ),
  },
  {
    title: "Extras, for the cards that need one",
    body: (
      <>
        <p>
          Every photograph here is deliberately flat and square, and that is exactly what makes
          a holo look like a matte card. <strong>Extras</strong> walks the pile one card at a
          time so you can tilt the few that need it and shoot the foil catching the light.
        </p>
        <p className="muted">
          Most cards want <strong>Skip</strong>. It is one pass, and skipping is one key.
        </p>
      </>
    ),
  },
  {
    title: "Check it, download it, start the next pile",
    body: (
      <>
        <p>
          <strong>Batch</strong> shows the pile you are on, large enough to spot a crop that
          clipped a corner or a card shot back-first. Both are fixable there.
        </p>
        <p>
          Then <strong>Download all photos</strong> gives you a folder to hand to whatever is
          doing the listing, and <strong>Archive &amp; start next</strong> clears the screen
          for the next pile. Nothing is deleted — old batches stay one click away.
        </p>
        <p className="muted">
          The bar tells you how many photographs each card has. Bulk uploaders need that number
          to be the same for every card, so if a batch has two different counts it says so.
        </p>
      </>
    ),
  },
];

export default function Intro({
  startAt = 0,
  onStep,
  onDone,
}: {
  startAt?: number;
  onStep?: (step: number) => void;
  onDone: () => void;
}) {
  const [step, setStep] = useState(Math.min(Math.max(startAt, 0), STEPS.length - 1));
  const last = step === STEPS.length - 1;

  const go = (next: number) => {
    setStep(next);
    onStep?.(next);
  };

  return (
    <div className="intro">
      <div className="intro-card">
        <div className="intro-dots" aria-hidden="true">
          {STEPS.map((s, i) => (
            <span key={s.title} className={i === step ? "on" : i < step ? "done" : ""} />
          ))}
        </div>

        <h2>{STEPS[step].title}</h2>
        <div className="intro-body">{STEPS[step].body}</div>

        <div className="intro-actions">
          <button className="linklike" onClick={onDone}>
            {last ? "" : "skip"}
          </button>
          <span className="spacer" />
          {step > 0 ? (
            <button onClick={() => go(step - 1)}>Back</button>
          ) : null}
          <button className="intro-next" onClick={() => (last ? onDone() : go(step + 1))}>
            {last ? "Start scanning" : "Next"}
          </button>
        </div>
      </div>
    </div>
  );
}

export const INTRO_STEPS = STEPS.length;
