// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Motherstock Plan", {
	refresh(frm) {
		if (frm.doc.production_plan) {
			frm.add_custom_button(__("Rebuild"), () => {
				frm.call("rebuild").then(() => frm.save());
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
	divert_weeks(frm) { frm.set_value("schedule", []); },
});
