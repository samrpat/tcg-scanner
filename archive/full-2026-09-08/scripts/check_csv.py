"""Invariants for the eBay upload file, read on stdin.

The file's columns are whatever template the operator downloaded (D-134), so this cannot check
for a fixed list of headers. What it checks instead are the things that are true of any eBay
upload: the header row exists, every row lines up with it, titles fit, a price column that
exists is filled, and the description never promises photographs the row does not carry.
"""

import csv
import io
import sys

sys.path.insert(0, "api")

GREEN, RED, OFF = "\033[32m", "\033[31m", "\033[0m"
PHOTO_WORDS = ("photograph", "picture", "image")


def find_header(rows: list[list[str]]) -> int:
    """eBay's templates open with instruction and #INFO lines before the real header."""
    for index, row in enumerate(rows):
        joined = " ".join(row).lower()
        if row and not row[0].startswith("#") and ("title" in joined or "action" in joined):
            return index
    return -1


def column(headers: list[str], *needles: str) -> str | None:
    for header in headers:
        flat = header.lower().replace(" ", "")
        for needle in needles:
            if needle.replace(" ", "") in flat:
                return header
    return None


def main() -> int:
    rows = list(csv.reader(io.StringIO(sys.stdin.read())))
    start = find_header(rows)
    if start < 0:
        print(f"  {RED}✗{OFF} no header row found in the export")
        return 1

    headers = rows[start]
    data = [r for r in rows[start + 1 :] if any(cell.strip() for cell in r)]
    problems: list[str] = []

    title_col = column(headers, "title")
    price_col = column(headers, "startprice", "price")
    photo_col = column(headers, "photourl", "picurl")
    desc_col = column(headers, "description")
    sku_col = column(headers, "customlabel", "sku")

    # eBay marks required columns with `*`. An empty one is a row it rejects on import.
    required = [h for h in headers if h.startswith("*")]

    for row in data:
        if len(row) != len(headers):
            problems.append(f"row has {len(row)} cells against {len(headers)} columns")
            continue
        cells = dict(zip(headers, row, strict=True))
        who = cells.get(sku_col or "", "?")

        if title_col and len(cells.get(title_col, "")) > 80:
            problems.append(f"{who}: title over 80 characters")
        # "Purchase Price" is a cost basis, not a sale price, and is correctly left empty.
        if price_col and "purchase" not in price_col.lower() and not cells.get(price_col):
            problems.append(f"{who}: no price")
        for header in required:
            if not cells.get(header, "").strip():
                problems.append(f"{who}: required column {header[:40]} is empty")

        if photo_col and desc_col:
            described = cells.get(desc_col, "").lower()
            if not cells.get(photo_col) and any(w in described for w in PHOTO_WORDS):
                problems.append(f"{who}: mentions photos but has no photo URL")

    for problem in problems:
        print(f"  {RED}✗{OFF} {problem}")
    if not problems:
        print(
            f"  {GREEN}✓{OFF} {len(data)} rows against {len(headers)} template columns: "
            f"aligned, {len(required)} required filled, titles fit, no unbacked photo claims"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
