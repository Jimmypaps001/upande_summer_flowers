"""Stop a Client Script erroring on fields Crop Cycle has not got.

"Calculate Zerobending and Production Dates" sets expected_production_date and
variety_yield on Crop Cycle. Neither field exists -- Frappe raises "Field
variety_yield not found", the script's own catch turns that into "Error
fetching variety yield from Item", and the reader sees it every time a variety
or a planting date is touched.

This is a site record, not app code, so nothing here deletes the intent. Each
set is guarded on the field actually being there: the day somebody adds the
fields, the script starts working again on its own.
"""

import frappe

NAME = "Calculate Zerobending and Production Dates"

GUARD = """// Guarded: both fields these handlers set were missing from Crop Cycle, so
// every variety and planting-date change raised "Field ... not found" and the
// catch below reported it as a fetch failure. Add the fields and this works
// again with no further change.
function sf_has(frm, field) {
    return !!(frm.fields_dict && frm.fields_dict[field]);
}

"""


def run(apply=0):
    from frappe.utils import cint
    apply = cint(apply)
    doc = frappe.get_doc("Client Script", NAME)
    s = doc.script
    changes = []

    for field in ("expected_production_date", "variety_yield"):
        for value in ("expectedDate", "item.custom_yield", "null"):
            old = "frm.set_value('%s', %s);" % (field, value)
            if old in s:
                new = ("if (sf_has(frm, '%s')) frm.set_value('%s', %s);"
                       % (field, field, value))
                s = s.replace(old, new)
                changes.append("guarded %s = %s" % (field, value))
    if "function sf_has(" not in s:
        s = GUARD + s
        changes.append("added the guard helper")

    if not changes:
        print("nothing to change")
        return
    print("%s:" % NAME)
    for c in changes:
        print("   %s" % c)
    if not apply:
        print("\n   (dry run -- pass apply=1 to save)")
        return
    doc.script = s
    doc.flags.ignore_permissions = True
    doc.save()
    frappe.db.commit()
    print("\n   saved. it now sets nothing that is not there.")
