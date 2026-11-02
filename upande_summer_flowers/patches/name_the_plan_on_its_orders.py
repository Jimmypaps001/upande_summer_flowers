"""Point every order already raised back at the plan that raised it.

The link is new, so the requests and orders a plan raised before it existed
know nothing about it and the plan's Connections tab would open empty on every
season already bought. The fact was never missing -- the plan's own requirement
rows record which request each line became -- it was only recorded one way
round. This writes the other way.

Purchase orders are reached through the request they were made from, which is
how ERPNext joins them, so an order raised by hand off one of these requests is
picked up as well.
"""

import frappe


def execute():
    frappe.reload_doc("summer_flowers", "doctype", "summer_flower_procurement_plan")
    if not frappe.db.has_column("Material Request", "custom_sf_procurement_plan"):
        return

    rows = frappe.get_all(
        "Planting Material Requirement",
        filters={"parenttype": "Summer Flower Procurement Plan",
                 "source_doctype": "Material Request", "source_doc": ["is", "set"]},
        fields=["parent", "source_doc"])
    plan_of = {}
    for r in rows:
        plan_of.setdefault(r.source_doc, r.parent)
    if not plan_of:
        return

    done = 0
    for mr, plan in plan_of.items():
        if not frappe.db.exists("Material Request", mr):
            continue
        if frappe.db.get_value("Material Request", mr, "custom_sf_procurement_plan"):
            continue
        frappe.db.set_value("Material Request", mr, "custom_sf_procurement_plan",
                            plan, update_modified=False)
        done += 1

    orders = 0
    if frappe.db.has_column("Purchase Order", "custom_sf_procurement_plan"):
        for po, mr in frappe.get_all(
                "Purchase Order Item",
                filters={"material_request": ["in", list(plan_of)],
                         "docstatus": ["<", 2]},
                fields=["parent", "material_request"], as_list=True):
            if frappe.db.get_value("Purchase Order", po,
                                   "custom_sf_procurement_plan"):
                continue
            frappe.db.set_value("Purchase Order", po, "custom_sf_procurement_plan",
                                plan_of.get(mr), update_modified=False)
            orders += 1
    frappe.db.commit()
    print("Named the plan on %d material requests and %d purchase orders."
          % (done, orders))
