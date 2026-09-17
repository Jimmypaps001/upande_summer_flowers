# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Carry crop protocols from one site to another.

A protocol is data, so a deploy does not bring it. This writes the summer flower
fields, the material route, the flush schedule and the grade split to a JSON file
on one site, and reads that file back on another -- creating what is missing and
updating what is there, without touching anything the target site knows that the
source does not.

    # on the site that has them
    bench --site <from> execute \\
      upande_summer_flowers.summer_flowers.console.move_protocols.export_them \\
      --args '["Eryngium", "/tmp/eryngium.json"]'

    # on the site that needs them
    bench --site <to> execute \\
      upande_summer_flowers.summer_flowers.console.move_protocols.import_them \\
      --args '["/tmp/eryngium.json", 1]'

The last argument is dry_run: 1 says what would happen, 0 does it.
"""

import json
import traceback

import frappe
from frappe.utils import cint

TABLES = ("custom_sf_material_route", "custom_sf_flush_schedule",
          "custom_sf_grade_allocation")
SKIP = {"name", "owner", "creation", "modified", "modified_by", "docstatus", "idx",
        "doctype", "parent", "parenttype", "parentfield", "_user_tags", "_comments",
        "_assign", "_liked_by"}
# The protocol's own bookkeeping. It describes this site's approval history, not
# the crop, and carrying it would make the target think it had approved things.
MACHINERY = {"custom_sf_protocol_status", "custom_sf_current_version",
             "custom_sf_pending_changes", "custom_version_count",
             "custom_sf_approved_by", "custom_sf_approved_on",
             "custom_farms_with_versions", "custom_sf_change_reason"}


def _clean(row):
    return {k: v for k, v in row.items() if k not in SKIP and v not in (None, "")}


def export_them(like, path):
    try:
        names = frappe.db.sql("""select name from `tabCrop Protocol`
            where variety like %s order by variety, farm""", "%%%s%%" % like, pluck=True)
        out = []
        for n in names:
            d = frappe.get_doc("Crop Protocol", n)
            row = {k: v for k, v in d.as_dict().items()
                   if k not in SKIP and k not in MACHINERY
                   and not isinstance(v, list) and v not in (None, "")}
            for t in TABLES:
                rows = [_clean(r.as_dict()) for r in (d.get(t) or [])]
                if rows:
                    row[t] = rows
            out.append(row)
        with open(path, "w") as f:
            json.dump(out, f, indent=1, default=str)
        print("wrote %d protocols matching %r to %s" % (len(out), like, path))
        for r in out:
            print("   %-46s route=%-2s flush=%-3s"
                  % (r.get("name_hint") or "%s-%s" % (r.get("variety"), r.get("farm")),
                     len(r.get("custom_sf_material_route") or []),
                     len(r.get("custom_sf_flush_schedule") or [])))
    except Exception:
        traceback.print_exc()


def import_them(path, dry_run=1):
    try:
        _import(path, cint(dry_run))
    except Exception:
        traceback.print_exc()


def _import(path, dry_run):
    with open(path) as f:
        rows = json.load(f)
    print("%d protocols in %s%s" % (len(rows), path, "  (DRY RUN)" if dry_run else ""))
    made = updated = blocked = 0
    for r in rows:
        variety, farm = r.get("variety"), r.get("farm")
        # A protocol names a variety at a farm. Both have to exist here, and
        # inventing either would attach a real crop to the wrong place.
        missing = [w for w, dt in ((variety, "Item"), (farm, "Farm"))
                   if not w or not frappe.db.exists(dt, w)]
        if missing:
            print("   BLOCKED %-40s no such %s on this site"
                  % ("%s-%s" % (variety, farm), " and ".join(missing)))
            blocked += 1
            continue
        name = frappe.db.get_value("Crop Protocol",
                                   {"variety": variety, "farm": farm}, "name")
        verb = "update" if name else "create"
        print("   %-7s %s" % (verb, name or "%s-%s" % (variety, farm)))
        if dry_run:
            made += verb == "create"
            updated += verb == "update"
            continue
        doc = frappe.get_doc("Crop Protocol", name) if name else frappe.new_doc("Crop Protocol")
        for k, v in r.items():
            if k in TABLES:
                doc.set(k, [])
                for child in v:
                    doc.append(k, child)
            else:
                doc.set(k, v)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.save()
        made += verb == "create"
        updated += verb == "update"
    if not dry_run:
        frappe.db.commit()
    print("\n%s: %d created, %d updated, %d blocked"
          % ("would be" if dry_run else "done", made, updated, blocked))
