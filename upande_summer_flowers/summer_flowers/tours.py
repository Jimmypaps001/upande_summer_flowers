import json
import os

import frappe

VOLATILE = ("name", "creation", "modified", "modified_by", "owner",
            "parent", "parentfield", "parenttype")


def sync_form_tours():
    path = os.path.join(os.path.dirname(__file__), "tour_data", "form_tour.json")
    with open(path) as f:
        tours = json.load(f)

    for tour in tours:
        try:
            dt = tour["reference_doctype"]
            if not frappe.db.exists("DocType", dt):
                print(f"Form Tour '{tour['name']}': DocType {dt} not found, skipped")
                continue

            meta = frappe.get_meta(dt)
            steps = []
            for s in tour.get("steps", []):
                if s.get("is_table_field") and s.get("parent_fieldname"):
                    parent = meta.get_field(s["parent_fieldname"])
                    ok = bool(parent and frappe.get_meta(parent.options).get_field(s.get("fieldname")))
                else:
                    ok = bool(meta.get_field(s.get("fieldname")))

                if ok:
                    steps.append({k: v for k, v in s.items() if k not in VOLATILE})
                else:
                    print(f"Form Tour '{tour['name']}': skipped step, field "
                          f"'{s.get('fieldname')}' not found on {dt}")

            data = {k: v for k, v in tour.items()
                    if k not in ("creation", "modified", "modified_by", "owner")}
            data["steps"] = steps

            if frappe.db.exists("Form Tour", tour["name"]):
                frappe.delete_doc("Form Tour", tour["name"], force=True, ignore_permissions=True)
            frappe.get_doc(data).insert(ignore_permissions=True)
        except Exception:
            frappe.log_error(title=f"Form Tour sync failed: {tour.get('name')}")
