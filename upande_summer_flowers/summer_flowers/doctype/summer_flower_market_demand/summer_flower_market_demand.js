// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Market Demand", {
	refresh(frm) {
		frm.set_query("variety", () => ({ filters: { item_group: "Summer Flowers" } }));

		if (frm.is_new()) return;

		if (frm.doc.horizon_gap_weeks > 0) {
			frm.dashboard.set_headline(
				__("Horizon is short by {0} weeks of the {1}-year target.", [
					frm.doc.horizon_gap_weeks,
					frm.doc.target_years_ahead,
				]),
				"orange"
			);
			frm.add_custom_button(__("Extend Horizon"), () => extend_horizon(frm)).addClass(
				"btn-primary"
			);
		} else if (frm.doc.weeks_covered) {
			frm.dashboard.set_headline(
				__("Horizon reaches {0} — {1} weeks ahead.", [
					frm.doc.horizon_end,
					frm.doc.weeks_ahead_of_today,
				]),
				"green"
			);
		}

		if (frm.doc.weeks_covered) {
			frm.add_custom_button(__("Create Production Plan"), () => create_plan(frm));
		}
	},
});

function extend_horizon(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Extend Demand Horizon"),
		fields: [
			{
				fieldname: "weeks",
				label: __("Weeks to add"),
				fieldtype: "Int",
				default: frm.doc.horizon_gap_weeks,
				reqd: 1,
			},
			{
				fieldname: "copy_from_last_year",
				label: __("Seed each week from the same week last year"),
				fieldtype: "Check",
				default: 1,
				description: __(
					"Starts the new weeks from last year's shape instead of zero. You still edit them."
				),
			},
		],
		primary_action_label: __("Extend"),
		primary_action(values) {
			d.hide();
			frm.call({
				doc: frm.doc,
				method: "extend_horizon",
				args: values,
				freeze: true,
				freeze_message: __("Extending horizon..."),
			}).then((r) => {
				if (r.message) {
					frappe.show_alert({ message: r.message.message, indicator: "green" });
				}
				frm.reload_doc();
			});
		},
	});
	d.show();
}

function create_plan(frm) {
	const first = frm.doc.demand_weeks[0];
	const d = new frappe.ui.Dialog({
		title: __("Create Production Plan"),
		fields: [
			{
				fieldname: "from_year",
				label: __("From Year"),
				fieldtype: "Int",
				default: first.year,
				reqd: 1,
			},
			{
				fieldname: "from_week",
				label: __("From Week"),
				fieldtype: "Int",
				default: first.week_no,
				reqd: 1,
			},
			{
				fieldname: "weeks",
				label: __("Weeks to cover"),
				fieldtype: "Int",
				default: Math.min(frm.doc.weeks_covered, 156),
				reqd: 1,
				description: __("156 weeks covers the full 3-year horizon."),
			},
		],
		primary_action_label: __("Create"),
		primary_action(values) {
			d.hide();
			frm.call({
				doc: frm.doc,
				method: "create_production_plan",
				args: values,
				freeze: true,
				freeze_message: __("Building production plan..."),
			}).then((r) => {
				if (r.message) {
					frappe.set_route("Form", "Summer Flower Production Plan", r.message);
				}
			});
		},
	});
	d.show();
}
