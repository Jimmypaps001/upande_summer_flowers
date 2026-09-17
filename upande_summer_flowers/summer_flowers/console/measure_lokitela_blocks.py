# Copyright (c) 2026, James Kiruga and contributors
# For license information, please see license.txt
"""Give Lokitela's blocks a plantable area, so a plan can be placed on them.

Lokitela has 1,872 beds and not one measurement: no bed length, width or area
anywhere on the farm. Its greenhouse records do carry an area -- 5.45 ha for a
block of 32 beds -- but that is the whole field unit including roads and
headlands, thirty-four times what the beds themselves can be. Using it would tell
a planner the farm can grow thirty times what it can.

So the beds are sized from Karen's, which are measured: 50 m² each, the figure on
Karen's own Block Bed rows. That is an assumption, and it is written onto each
block as one -- a stated assumption is worth more than a blank, and far more than
a number that looks measured and is not.

    bench --site <site> execute \\
      upande_summer_flowers.summer_flowers.console.measure_lokitela_blocks.main \\
      --args "[1]"
"""

import traceback

import frappe
from frappe.utils import cint, flt

FARM = "Lokitela"
BED_SQM = 50.0          # Karen's measured bed, and the protocol's own bed size
BED_WIDTH_M = 1.3       # Karen's beds average 1.28 m across


def main(dry_run=1):
    try:
        _run(cint(dry_run))
    except Exception:
        traceback.print_exc()


def _run(dry_run):
    blocks = frappe.get_all("Block", filters={"farm": FARM,
                                              "custom_is_summer_flower_block": 1},
                            fields=["name", "custom_total_beds", "custom_net_area_ha",
                                    "block_length", "block_width"])
    if not blocks:
        print("No summer flower blocks at %s." % FARM)
        return
    print("%s: %d blocks%s" % (FARM, len(blocks), "  (DRY RUN)" if dry_run else ""))
    done = 0
    for b in blocks:
        beds = cint(b.custom_total_beds)
        if not beds:
            print("   %-46s no beds, skipped" % b.name)
            continue
        if flt(b.custom_net_area_ha):
            print("   %-46s already measured, left alone" % b.name)
            continue
        net_sqm = beds * BED_SQM
        # A block of N beds laid side by side: width is the beds, length is a bed.
        width = round(beds * BED_WIDTH_M, 2)
        length = round(net_sqm / width, 2) if width else 0
        if dry_run:
            print("   %-46s %3d beds -> %6.0f m² (%.1f x %.1f m), %.2f ha"
                  % (b.name, beds, net_sqm, length, width, net_sqm / 10_000))
            done += 1
            continue
        doc = frappe.get_doc("Block", b.name)
        doc.block_length = length
        doc.block_width = width
        doc.block_area = net_sqm
        doc.custom_net_area_ha = net_sqm / 10_000
        doc.custom_bed_sync_note = (
            "Beds sized at %g m² each, from Karen's measured beds. Lokitela has no "
            "measurements of its own; replace these when the farm is surveyed."
            % BED_SQM)
        doc.flags.ignore_permissions = True
        doc.flags.ignore_mandatory = True
        doc.save()
        done += 1
        print("   %-46s %3d beds -> %.2f ha" % (b.name, beds, net_sqm / 10_000))

    if not dry_run:
        frappe.db.commit()
    print("\n%s %d blocks; %.2f ha in total"
          % ("would measure" if dry_run else "measured", done,
             sum(cint(b.custom_total_beds) * BED_SQM for b in blocks) / 10_000))
