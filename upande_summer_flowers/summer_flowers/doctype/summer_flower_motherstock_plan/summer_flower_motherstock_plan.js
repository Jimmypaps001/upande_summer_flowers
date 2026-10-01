// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Motherstock Plan", {
	refresh(frm) {
		if (frm.doc.production_plan) {
			frm.add_custom_button(__("Rebuild"), () => {
				frm.call("rebuild").then(() => frm.save());
			});
			// Where the line is actually worked out: the peak week, the weekly
			// split and the shortfall side by side. This document stores what
			// gets agreed there, and can still be edited directly.
			frm.add_custom_button(__("Plan on Dashboard"), () => {
				const q = new URLSearchParams({
					plan: frm.doc.production_plan,
					variety: frm.doc.variety || "",
					farm: frm.doc.farm || "",
				});
				window.open(
					`/summer-flowers-planning?${q.toString()}#motherstock`, "_blank");
			});
			frm.add_custom_button(__("Production Plan"), () =>
				frappe.set_route("Form", "Summer Flower Production Plan",
					frm.doc.production_plan));
		}
		// The one number nobody can check by eye, said where it is read.
		if (frm.doc.covers === 0 && frm.doc.shortfall) {
			frm.dashboard.set_headline(
				__("Short by {0} cuttings. {1} of {2} planting weeks are met.",
					[format_number(frm.doc.shortfall, null, 0),
					 frm.doc.weeks_met, frm.doc.weeks_required]));
		}
	},
	// Either knob changes the whole line, so nothing below them is left standing
	// from the previous answer.
	tc_to_order(frm) { frm.set_value("schedule", []); },
	// Multiplication sizes the order, so moving it clears the quantity and lets
	// Rebuild work a new one out. Keeping the old figure beside a count it was
	// never solved for is the one combination that cannot be meant.
	multiplications(frm) {
		frm.set_value("tc_to_order", 0);
		frm.set_value("schedule", []);
	},
});
