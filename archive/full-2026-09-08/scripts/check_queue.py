"""Invariants that must hold across the whole eBay queue, not just one card.

Reads the queue on stdin. Exits non-zero if any card is in a state the UI cannot render
sensibly — an over-long title eBay would reject, or a card marked listed that the queue also
says is not listable, which means two parts of the app disagree about the same card.
"""

import json
import sys

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"


def main() -> int:
    cards = json.load(sys.stdin)["cards"]
    problems = []
    for c in cards:
        title = c["title"] or ""
        if len(title) > 80:
            problems.append(f"{c['sku']} title is {len(title)} chars: {title}")
        if c["blockers"] and c["listed_at"]:
            problems.append(
                f"{c['sku']} is marked listed but still blocked: {'; '.join(c['blockers'])}"
            )
        if not c["blockers"] and not c["listed_at"] and c["price"] is None:
            problems.append(f"{c['sku']} counts as ready with no price")

    for p in problems:
        print(f"  {RED}✗{OFF} {p}")
    if not problems:
        print(f"  {GREEN}✓{OFF} {len(cards)} cards: titles within 80, states consistent")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
