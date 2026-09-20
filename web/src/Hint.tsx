/**
 * One line saying what a screen is for.
 *
 * Replaces a paragraph in the footer that explained all four tabs at once — which meant the
 * explanation was never next to the thing it explained, went stale the moment a tab was added
 * (it never mentioned Extras), and ended by telling the reader to edit an environment
 * variable they had no way to reach.
 *
 * Help belongs at the thing it is about, and it belongs in one line. Anything longer is the
 * interface apologising for itself.
 */
export default function Hint({ children }: { children: React.ReactNode }) {
  return <p className="screen-hint">{children}</p>;
}
