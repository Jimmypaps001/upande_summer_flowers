# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Read the market demand workbook the way the farm writes it.

"3 YEAR PINK FLUSH MARKET DEMAND.xlsx" is one block per financial year:

    Financial year  Crop   Variety            Grade split  Total   W27 W28 ... W26
    2026-27         Aster  Aster Pink Flush   50           310,000 5,000 ...
                                              60           930,000 15,000 ...
                                              70         1,085,000 17,500 ...
                                              80         1,085,000 17,500 ...
                                              Total      3,410,000 55,000 ...

Three things about that layout decide everything here.

The year is a FINANCIAL year, July to June, so its weeks run W27 to W52 (or W53)
and then W1 to W26 of the following calendar year. Reading the week number
without that split puts eleven months of demand in the wrong year.

A 53-week year is real: 2026-27 has one and the other two do not. The count is a
property of the ISO calendar, not a mistake, and a year given a W53 it does not
have is a mistake -- so both are checked rather than assumed.

The rows are grades and the last one is their total. Demand is stored per week,
not per grade -- the split lives on the crop protocol, where it is used to price
and to size -- so the grades are read to check the arithmetic and to report the
split, and it is the Total row that becomes the register.

Nothing here writes. parse and preview read; only import_demand writes, and it
says what it did.
"""

import base64
import csv
import datetime
import io

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from upande_summer_flowers.summer_flowers.planning import iso_monday

HEADER_FIRST_CELL = "financial year"
TOTAL_LABEL = "total"


def _num(v):
    """A cell as an integer. The workbook writes numbers as text with commas."""
    if v in (None, ""):
        return None
    if isinstance(v, (int, float)):
        return int(round(v))
    s = str(v).strip().replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def iso_weeks_in_year(year):
    """53 if that ISO year has a 53rd week, else 52.

    A year has 53 ISO weeks when 1 January falls on a Thursday, or on a Wednesday
    in a leap year -- equivalently, when 28 December lands in week 53.
    """
    return datetime.date(int(year), 12, 28).isocalendar()[1]


def _rows_from_xlsx(content):
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True)
    ws = wb[wb.sheetnames[0]]
    return [[c.value for c in row] for row in ws.iter_rows()]


def _rows_from_text(content):
    text = content.decode("utf-8-sig", errors="replace")
    # Tab first: pasting out of a spreadsheet gives tabs, and a comma inside a
    # thousands separator would tear a CSV row apart.
    delim = "\t" if "\t" in text.split("\n")[0] else ","
    return [r for r in csv.reader(io.StringIO(text), delimiter=delim)]


def parse(content, filename=""):
    """The workbook as blocks, one per financial year. Reads only."""
    if isinstance(content, str):
        content = base64.b64decode(content)
    rows = (_rows_from_xlsx(content) if filename.lower().endswith((".xlsx", ".xlsm"))
            else _rows_from_text(content))

    blocks, i = [], 0
    while i < len(rows):
        row = rows[i]
        first = str((row[0] if row else "") or "").strip().lower()
        if first != HEADER_FIRST_CELL:
            i += 1
            continue

        # Week columns, in the order the sheet gives them, with the column each
        # sits in -- a block may be 52 or 53 weeks wide.
        weeks = []
        for col in range(5, len(row)):
            label = str(row[col] or "").strip().upper()
            if label.startswith("W") and label[1:].isdigit():
                weeks.append((col, int(label[1:])))

        block = {"weeks": weeks, "grades": [], "total_row": None,
                 "financial_year": None, "crop": None, "variety": None,
                 "header_row": i + 1}
        i += 1
        while i < len(rows):
            r = rows[i]
            if not r or str((r[0] if r else "") or "").strip().lower() == HEADER_FIRST_CELL:
                break
            label = str((r[3] if len(r) > 3 else "") or "").strip()
            if not label:
                i += 1
                if all(str(c or "").strip() == "" for c in r):
                    break
                continue
            block["financial_year"] = block["financial_year"] or str(
                (r[0] if r else "") or "").strip() or None
            block["crop"] = block["crop"] or str(
                (r[1] if len(r) > 1 else "") or "").strip() or None
            block["variety"] = block["variety"] or str(
                (r[2] if len(r) > 2 else "") or "").strip() or None
            entry = {
                "label": label,
                "stated_total": _num(r[4] if len(r) > 4 else None),
                "weekly": [(_num(r[c]) or 0) if c < len(r) else 0 for c, _w in weeks],
            }
            if label.lower() == TOTAL_LABEL:
                block["total_row"] = entry
            else:
                block["grades"].append(entry)
            i += 1
        if block["financial_year"] and block["weeks"]:
            blocks.append(block)
    return blocks


def _dated_weeks(block):
    """Week numbers to (iso_year, week_no, monday), splitting at the July boundary."""
    start = cint(str(block["financial_year"]).split("-")[0])
    out = []
    for (_col, w) in block["weeks"]:
        year = start if w >= 27 else start + 1
        out.append((year, w, iso_monday(year, w)))
    return out


def check(blocks):
    """Everything worth objecting to, before anything is written."""
    notes, problems = [], []
    for b in blocks:
        fy = b["financial_year"]
        tot = b["total_row"]
        if not tot:
            problems.append(_("{0} has no Total row.").format(fy))
            continue
        n = len(b["weeks"])
        summed = [sum(g["weekly"][i] for g in b["grades"]) for i in range(n)]
        if summed != tot["weekly"]:
            bad = [b["weeks"][i][1] for i in range(n) if summed[i] != tot["weekly"][i]]
            problems.append(_("{0}: the grades do not add up to the Total row in "
                              "week(s) {1}.").format(fy, ", ".join("W%d" % w for w in bad)))
        if tot["stated_total"] is not None and sum(tot["weekly"]) != tot["stated_total"]:
            problems.append(_("{0}: the Total column says {1} but its weeks add to "
                              "{2}.").format(fy, tot["stated_total"], sum(tot["weekly"])))

        # A 53rd week the ISO calendar has not got would silently land in the next
        # year, which is how a year of demand goes missing.
        start = cint(str(fy).split("-")[0])
        has53 = [w for (_c, w) in b["weeks"] if w == 53]
        if has53 and iso_weeks_in_year(start) != 53:
            problems.append(_("{0}: the sheet gives a W53, but ISO {1} has only {2} "
                              "weeks.").format(fy, start, iso_weeks_in_year(start)))
        if not has53 and iso_weeks_in_year(start) == 53:
            notes.append(_("{0}: ISO {1} has 53 weeks and the sheet gives 52. Week 53 "
                           "will carry no demand.").format(fy, start))
    return notes, problems


@frappe.whitelist()
def preview(filename, content):
    """What the file says and what importing it would do. Writes nothing."""
    if frappe.session.user == "Guest":
        frappe.throw(_("Please sign in."), frappe.PermissionError)
    blocks = parse(content, filename)
    if not blocks:
        frappe.throw(_("No 'Financial year' header found. This reads the layout with "
                       "Financial year, Crop, Variety, Grade split, Total, then W27 "
                       "onwards."))

    notes, problems = check(blocks)
    variety = next((b["variety"] for b in blocks if b["variety"]), None)
    known = bool(variety and frappe.db.exists("Item", variety))
    existing = frappe.db.get_value(
        "Summer Flower Market Demand", {"variety": variety}, "name") if known else None

    have = set()
    if existing:
        have = {(cint(r.year), cint(r.week_no)) for r in frappe.get_all(
            "Summer Flower Demand Week", filters={"parent": existing},
            fields=["year", "week_no"])}

    out_blocks = []
    for b in blocks:
        dated = _dated_weeks(b)
        tot = b["total_row"] or {"weekly": [0] * len(dated), "stated_total": 0}
        clash = sum(1 for (y, w, _m) in dated if (y, w) in have)
        split = []
        grand = sum(tot["weekly"]) or 1
        for g in b["grades"]:
            split.append({"grade": g["label"], "stems": sum(g["weekly"]),
                          "pct": round(sum(g["weekly"]) * 100.0 / grand, 2)})
        out_blocks.append({
            "financial_year": b["financial_year"],
            "crop": b["crop"], "variety": b["variety"],
            "weeks": len(dated),
            "first": "%s-W%02d" % (dated[0][0], dated[0][1]),
            "last": "%s-W%02d" % (dated[-1][0], dated[-1][1]),
            "total": sum(tot["weekly"]),
            "stated_total": tot["stated_total"],
            "peak": max(tot["weekly"]) if tot["weekly"] else 0,
            "already_on_register": clash,
            "grades": split,
        })

    if variety and not known:
        problems.append(_("There is no variety called '{0}' on this site. Check the "
                          "spelling, or pick the variety to import it as.").format(variety))
    return {
        "filename": filename,
        "variety": variety,
        "variety_known": known,
        "market_demand": existing,
        "blocks": out_blocks,
        "total": sum(b["total"] for b in out_blocks),
        "weeks": sum(b["weeks"] for b in out_blocks),
        "notes": notes,
        "problems": problems,
        "can_import": not problems,
    }


@frappe.whitelist()
def import_demand(filename, content, variety=None, mode="replace"):
    """Write the Total row of every block into the variety's demand register.

    mode=replace clears the weeks the file covers and writes the file's figures --
    the file is the statement of record for the years it names. mode=add leaves
    every week already there alone and fills only the gaps.

    Grades are not written. The split is the protocol's, where it prices and sizes
    the crop; storing a second copy here is how the two come to disagree.
    """
    if frappe.session.user == "Guest":
        frappe.throw(_("Please sign in."), frappe.PermissionError)
    blocks = parse(content, filename)
    if not blocks:
        frappe.throw(_("Nothing to import."))
    notes, problems = check(blocks)
    if problems:
        frappe.throw("<br>".join(problems), title=_("The file does not add up"))

    variety = variety or next((b["variety"] for b in blocks if b["variety"]), None)
    if not variety or not frappe.db.exists("Item", variety):
        frappe.throw(_("Pick a variety that exists on this site."))

    name = frappe.db.get_value("Summer Flower Market Demand", {"variety": variety}, "name")
    if name:
        d = frappe.get_doc("Summer Flower Market Demand", name)
        created = False
    else:
        d = frappe.new_doc("Summer Flower Market Demand")
        d.variety = variety
        created = True

    incoming = {}
    for b in blocks:
        tot = b["total_row"]
        for (y, w, monday), stems in zip(_dated_weeks(b), tot["weekly"]):
            incoming[(y, w)] = {"stems": cint(stems), "monday": monday}

    before = len(d.demand_weeks)
    if mode == "replace":
        kept = [r for r in d.demand_weeks
                if (cint(r.year), cint(r.week_no)) not in incoming]
        removed = before - len(kept)
        d.demand_weeks = []
        for r in kept:
            d.append("demand_weeks", {
                "year": r.year, "week_no": r.week_no,
                "week_start_date": r.week_start_date,
                "demand_stems": r.demand_stems, "is_firm": r.is_firm,
                "notes": r.notes})
        added = 0
        for (y, w), v in sorted(incoming.items()):
            d.append("demand_weeks", {"year": y, "week_no": w,
                                      "week_start_date": v["monday"],
                                      "demand_stems": v["stems"], "is_firm": 0})
            added += 1
        skipped = 0
    else:
        have = {(cint(r.year), cint(r.week_no)) for r in d.demand_weeks}
        added = skipped = removed = 0
        for (y, w), v in sorted(incoming.items()):
            if (y, w) in have:
                skipped += 1
                continue
            d.append("demand_weeks", {"year": y, "week_no": w,
                                      "week_start_date": v["monday"],
                                      "demand_stems": v["stems"], "is_firm": 0})
            added += 1

    d.flags.ignore_permissions = True
    d.save()
    frappe.db.commit()
    d.reload()
    return {
        "market_demand": d.name,
        "variety": variety,
        "created": created,
        "mode": mode,
        "weeks_written": added,
        "weeks_replaced": removed,
        "weeks_skipped": skipped,
        "weeks_covered": cint(d.weeks_covered),
        "horizon_start": d.horizon_start,
        "horizon_end": d.horizon_end,
        "total_demand_stems": cint(d.total_demand_stems),
        "notes": notes,
    }
