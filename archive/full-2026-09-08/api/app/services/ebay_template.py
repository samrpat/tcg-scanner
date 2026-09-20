"""Matching this system's values onto eBay's own template columns.

eBay's Seller Hub Reports hands the seller a template, and its columns are the ones it will
accept. They differ between template types and categories, and they are not always what the
File Exchange documentation implies. Generating a file from conventions and hoping is a guess
that fails at the point of upload — after all the work is done.

So the operator's downloaded template is parsed, and the export emits exactly its columns, in
its order. The matching is deliberately forgiving about how a header is *written* and strict
about what it *means*: eBay writes the same column as "Item photo URL", "PicURL" and
"Item Photo URL" across templates, and a listing that loses its photographs to a capitalisation
difference is the failure this whole module exists to prevent.
"""

from __future__ import annotations

import csv
import io
import re

# Our internal field name -> the header spellings eBay has used for it. Compared after
# normalisation, so case, spacing and punctuation do not matter.
ALIASES: dict[str, tuple[str, ...]] = {
    "action": ("action", "*action",),
    "sku": ("customlabel", "custom label", "customlabelsku", "sku"),
    "category": ("category", "categoryid", "category id", "*categoryid", "categoryname"),
    "title": ("title", "*title", "listingtitle"),
    "description": ("description", "*description", "itemdescription"),
    "condition_id": ("conditionid", "condition id", "*conditionid", "condition"),
    "pics": ("picurl", "item photo url", "itemphotourl", "photourl", "pictureurl", "images"),
    "quantity": ("quantity", "*quantity", "availablequantity"),
    "format": ("format", "*format", "listingformat", "listingtype"),
    "price": ("startprice", "start price", "*startprice", "price", "buyitnowprice"),
    "best_offer": ("bestofferenabled", "best offer enabled", "bestoffer"),
    "duration": ("duration", "*duration", "listingduration"),
    "location": ("location", "itemlocation"),
    "postal_code": ("postalcode", "postal code", "zipcode", "zip code"),
    "epid": ("p:epid", "epid", "productreferenceid", "ebayproductid"),
    # Single cards have no barcode. eBay's own value for that is the literal string
    # "Does not apply"; leaving it blank makes the listing look unfinished and can trip
    # the product-identifier requirement in some categories.
    "upc": ("upc", "*upc", "upc/ean", "productid"),
    # ── My Collection (trading card) template ───────────────────────────────
    # A different shape entirely: it describes a card you own rather than a listing, so it
    # names the aspects as bare columns and has no Action, no photographs and no sale price.
    "game": ("game", "game (required field)"),
    "graded": ("graded", "graded (y/n)"),
    # `CD:` columns are eBay's condition *descriptors*, separate from ConditionID. For an
    # ungraded card only the condition itself applies; the grading ones describe a slab.
    "card_condition": ("cardcondition", "card condition", "cd:cardcondition"),
    "year": ("yearmanufactured", "year manufactured", "year"),
    # ── Category listing template (the one with both specifics and photographs) ──
    # eBay marks required columns with `*`; `normalise` strips it, so these match either way.
    "store_category": ("storecategory",),
    "schedule_time": ("scheduletime", "schedule time"),
    "buy_it_now": ("buyitnowprice", "buy it now price"),
    "dispatch_days": ("dispatchtimemax", "dispatch time max", "handlingtime"),
    "returns": ("returnsacceptedoption", "returns accepted option"),
    "shipping_type": ("shippingtype",),
    "shipping_service": ("shippingservice-1:option", "shippingservice1option"),
    "shipping_cost": ("shippingservice-1:cost", "shippingservice1cost"),
    "immediate_pay": ("immediatepayrequired",),
    # Deliberately absent from the writer: "Purchase Price" is what *you paid*, not what the
    # card is worth. Writing an estimate there would silently falsify a cost basis.
}

# Columns that name an item specific directly, without eBay's `C:` prefix. The collection
# template does this; the listing templates do not.
BARE_ASPECTS: dict[str, str] = {
    "cardname": "Card Name",
    "set": "Set",
    "rarity": "Rarity",
    "finish": "Finish",
    "cardnumber": "Card Number",
    "language": "Language",
    "features": "Features",
    "hp": "HP",
    "illustrator": "Illustrator",
    "manufacturer": "Manufacturer",
    "stage": "Stage",
    "cardtype": "Card Type",
    "_end": "",
    "shipping_profile": (
        "shippingprofilename", "shipping profile name", "shippingpolicy", "shippingpolicyname",
    ),
    "return_profile": (
        "returnprofilename", "return profile name", "returnpolicy", "returnpolicyname",
    ),
    "payment_profile": (
        "paymentprofilename", "payment profile name", "paymentpolicy", "paymentpolicyname",
    ),
}


def normalise(header: str) -> str:
    """Compare headers by meaning, not by punctuation.

    eBay marks required columns with a leading `*`, brackets a version string onto Action, and
    varies spacing and case between templates. None of that changes which column it is.
    """
    text = (header or "").strip().lower()
    # "Action(SiteID=US|Country=US|…)" is the Action column.
    text = re.sub(r"\(.*?\)", "", text)
    return re.sub(r"[^a-z0-9:]+", "", text)


def field_for(header: str) -> str | None:
    """Which of our values belongs in this column, if any."""
    key = normalise(header)
    if not key:
        return None
    for field, spellings in ALIASES.items():
        if key in {normalise(s) for s in spellings}:
            return field
    # Item specifics are prefixed `C:` in the listing templates.
    if key.startswith("c:"):
        return f"aspect:{header.split(':', 1)[1].strip()}"
    # …and named bare in the collection template.
    if key in BARE_ASPECTS and BARE_ASPECTS[key]:
        return f"aspect:{BARE_ASPECTS[key]}"
    # Grading descriptors describe a slab, not a raw card. Recognised so they are not reported
    # as unknown columns, and deliberately never filled.
    if key.startswith(("cd:", "cda:")):
        return None
    return None


def rows_from_xlsx(payload: bytes) -> list[list[str]]:
    """The first sheet of an .xlsx, as rows of strings.

    eBay hands out some templates as Excel and others as CSV, and which one you get depends on
    the button rather than on anything meaningful. Making the operator convert by hand is a
    step that exists only because this code could not read a zip file.
    """
    import io as _io

    from openpyxl import load_workbook

    book = load_workbook(_io.BytesIO(payload), read_only=True, data_only=True)
    sheet = book[book.sheetnames[0]]
    rows = [
        ["" if cell is None else str(cell) for cell in row]
        for row in sheet.iter_rows(values_only=True)
    ]
    book.close()
    return rows


def parse_rows(rows: list[list[str]]) -> dict:
    """Find the header row among already-parsed rows. See `parse_template`."""
    for index, row in enumerate(rows):
        if not row:
            continue
        recognised = sum(1 for cell in row if field_for(cell))
        if recognised >= 2:
            preamble = "\n".join(",".join(r) for r in rows[:index])
            return {
                "headers": [cell.strip() for cell in row],
                "preamble": preamble or None,
                "row_index": index,
                "recognised": recognised,
            }
    raise ValueError(
        "could not find a header row — is this an eBay template? Every column in it was "
        "unfamiliar."
    )


def parse_template(text: str) -> dict:
    """Find the header row in a downloaded template and keep what precedes it.

    eBay's files open with instruction lines and an `#INFO` block before the real header. The
    header is the first row that names a column we recognise — looking for a specific string
    would break the moment eBay reworded its preamble.
    """
    # Two independent hits is enough to be sure a row is the header, and low enough to survive
    # a sparse template that only requires Action and Category.
    # `newline=""` because eBay's Info rows carry raw newlines inside unquoted fields, which
    # the default reader treats as a fatal error rather than as the mess it is.
    return parse_rows(list(csv.reader(io.StringIO(text, newline=""))))


def row_for(headers: list[str], values: dict, aspects: dict) -> dict:
    """Lay one card's values out under the template's own column names.

    Columns we have nothing for are written empty rather than omitted: eBay reads by position
    as well as by name, and a short row is a misaligned row.
    """
    out: dict[str, str] = {}
    for header in headers:
        field = field_for(header)
        if field is None:
            out[header] = ""
        elif field.startswith("aspect:"):
            out[header] = aspects.get(field.split(":", 1)[1], "")
        else:
            out[header] = values.get(field, "")
    return out


def unmatched(headers: list[str], values: dict, aspects: dict) -> list[str]:
    """What this system knows that the template has no column for.

    Reported rather than dropped in silence — a missing Rarity column is a listing that will
    not show up under a rarity filter, and the operator should hear that from here rather than
    work it out from traffic.
    """
    covered = {field_for(h) for h in headers}
    missing = [k for k in values if k not in covered and values.get(k)]
    missing += [
        f"C:{name}"
        for name in aspects
        if f"aspect:{name}" not in covered and aspects.get(name)
    ]
    return sorted(missing)
