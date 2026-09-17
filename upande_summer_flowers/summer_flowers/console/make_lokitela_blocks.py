# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Draw a summer flower block over each of Lokitela's greenhouses.

Lokitela's 1,872 beds sit in 77 greenhouses that are already named as blocks --
MLIMA BLK 2, DAIRY BLK 1 -- at roughly 36 beds each, which is the size Karen's
real blocks run. One block per greenhouse therefore matches how the farm already
divides its ground rather than imposing a shape on it.

    bench --site <site> execute \\
      upande_summer_flowers.summer_flowers.console.make_lokitela_blocks.main --args "[1]"

The argument is dry_run: 1 says what would be made, 0 makes it.
"""

import traceback

import frappe
from frappe.utils import cint, flt

FARM = "Lokitela"


def main(dry_run=1):
    try:
        _run(cint(dry_run))
    except Exception:
        traceback.print_exc()


def _run(dry_run):
    rows = frappe.db.sql("""
        select b.greenhouse, count(*) beds, ifnull(sum(b.bed_area), 0) area
        from tabBed b join tabWarehouse w on w.name = b.greenhouse
        where w.custom_farm = %s and ifnull(b.custom_block, '') = ''
        group by b.greenhouse order by b.greenhouse
    """, FARM, as_dict=True)
    if not rows:
        print("No beds at %s belong to no block." % FARM)
        return

    print("%s: %d greenhouses, %d beds%s"
          % (FARM, len(rows), sum(r.beds for r in rows),
             "  (DRY RUN)" if dry_run else ""))
    made = 0
    for r in rows:
        existing = frappe.db.get_value("Block", {"farm": FARM, "greenhouse": r.greenhouse},
                                       "name")
        if existing:
            print("   already blocked: %-34s -> %s" % (r.greenhouse, existing))
            continue
        # The greenhouse name already carries the farm's own block label, so the
        # block takes it rather than inventing a second numbering nobody uses.
        label = r.greenhouse.replace(" - KL", "").strip()
        if dry_run:
            print("   would create: %-34s %3d beds, %.2f ha" % (label, r.beds,
                                                                flt(r.area) / 10_000))
            made += 1
            continue
        doc = frappe.new_doc("Block")
        doc.farm = FARM
        doc.greenhouse = r.greenhouse
        doc.block = label
        doc.custom_is_summer_flower_block = 1
        doc.custom_active = 1
        doc.custom_total_beds = cint(r.beds)
        doc.custom_beds_free = cint(r.beds)
        doc.custom_net_area_ha = flt(r.area) / 10_000
        doc.block_area = flt(r.area)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.insert()
        # The beds stop being loose: a bed in a block is ground a planting can go on.
        frappe.db.sql("update tabBed set custom_block = %s where greenhouse = %s "
                      "and ifnull(custom_block, '') = ''", (doc.name, r.greenhouse))
        made += 1
        print("   created %-44s %3d beds" % (doc.name, cint(r.beds)))

    if not dry_run:
        frappe.db.commit()
    print("\n%s %d blocks" % ("would create" if dry_run else "created", made))
    if not dry_run:
        from upande_summer_flowers.summer_flowers.doctype \
            .summer_flower_procurement_plan.summer_flower_procurement_plan import space_at
        print("space_at(%s) now -> %s" % (FARM, space_at(FARM)))
