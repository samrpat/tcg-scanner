/**
 * Is the person typing?
 *
 * Both camera screens bind Space and Enter to the shutter, so a cheap Bluetooth remote works
 * as a pedal. That is worth having and it is a trap: any text field on the same screen then
 * fires the camera on every space, and because the handler calls `preventDefault` the space
 * does not even reach the field. Naming a section became impossible to type and took a
 * photograph per word.
 *
 * Extras had a partial guard, Scan had none. A shared one, because a rule that has to be
 * remembered in each place is a rule that will be missed in the next place.
 *
 * `isContentEditable` covers the case neither screen has today and both might tomorrow.
 */
export function isTyping(event: Event): boolean {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  return (
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  );
}
