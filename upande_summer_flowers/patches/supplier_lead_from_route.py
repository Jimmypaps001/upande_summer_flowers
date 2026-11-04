"""The supplier's lead time, onto the protocols and versions that lost it."""

import frappe
from frappe.utils import cint


def execute():
    if not frappe.db.has_column("Crop Protocol", "custom_is_summer_flower"):
        return

    protocols = frappe.get_all("Crop Protocol",
                               filters={"custom_is_summer_flower": 1}, pluck="name")
    if not protocols:
        return

    done = _fill("Crop Protocol", protocols, "custom_sf_material_route",
                 "custom_sf_supplier_lead_weeks")
    versions = frappe.get_all("Crop Protocol Version",
                              filters={"crop_protocol": ["in", protocols]},
                              pluck="name")
    done_v = _fill("Crop Protocol Version", versions, "material_route",
                   "supplier_lead_weeks")
    bought = _mark_bought(protocols)
    frappe.db.commit()
    print("supplier lead read off the route for %d of %d protocols and %d of %d "
          "versions; %d way rows marked bought"
          % (done, len(protocols), done_v, len(versions), bought))


def _fill(doctype, names, table, field):
    """Give each record the lead of the first stage its route buys at."""
    if not names or not frappe.db.has_column(doctype, field):
        return 0
    rows = frappe.get_all("Crop Material Stage", filters={
        "parenttype": doctype, "parent": ["in", names], "is_purchase": 1},
        fields=["parent", "lead_weeks", "idx"], order_by="parent, idx")
    lead = {}
    for r in rows:
        if cint(r.lead_weeks) and r.parent not in lead:
            lead[r.parent] = cint(r.lead_weeks)
    done = 0
    for name, weeks in lead.items():
        if cint(frappe.db.get_value(doctype, name, field)):
            continue
        frappe.db.set_value(doctype, name, field, weeks, update_modified=False)
        done += 1
    return done


def _mark_bought(protocols):
    """A way whose route starts at a bought stage says so."""
    if not frappe.db.table_exists("Crop Protocol Way"):
        return 0
    from upande_summer_flowers.summer_flowers import stages as _stages
    from upande_summer_flowers.summer_flowers.crop_routes import ROUTES

    done = 0
    for w in frappe.get_all("Crop Protocol Way",
                            filters={"parenttype": "Crop Protocol",
                                     "parent": ["in", protocols]},
                            fields=["name", "route", "bought"]):
        entry = (ROUTES.get(w.route) or [None])[0]
        want = 1 if (entry and _stages.kind(entry) == _stages.BOUGHT) else 0
        if cint(w.bought) != want:
            frappe.db.set_value("Crop Protocol Way", w.name, "bought", want,
                                update_modified=False)
            done += 1
    return done
