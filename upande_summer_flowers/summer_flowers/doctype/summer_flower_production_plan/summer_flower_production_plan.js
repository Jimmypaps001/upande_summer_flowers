// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

frappe.ui.form.on("Summer Flower Production Plan", {
	refresh(frm) {
		// Only this crop at this farm. A protocol version for another variety would
		// produce numbers that look fine and mean nothing, so it is not offered.
		frm.set_query("protocol", () => ({
			filters: {
				variety: frm.doc.variety,
				farm: frm.doc.farm,
				version_status: ["in", ["Active", "Superseded"]],
			},
		}));

		if (frm.is_new()) return;

		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Regenerate"), () => {
				frappe.confirm(
					__("Rebuild the weekly grid and planting proposals from current demand and plantings?"),
					() =>
						frm
							.call({
								doc: frm.doc,
								method: "regenerate",
								freeze: true,
								freeze_message: __("Regenerating..."),
							})
							.then(() => frm.reload_doc())
				);
			});
		}

		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(__("Create Plantings"), () =>
				frm
					.call({
						doc: frm.doc,
						method: "create_plantings",
						freeze: true,
						freeze_message: __("Creating plantings..."),
					})
					.then((r) => {
						const m = r.message || {};
						frappe.msgprint({
							title: __("Plantings"),
							indicator: (m.created || []).length ? "green" : "orange",
							message:
								__("Created {0}.", [(m.created || []).length]) +
								((m.skipped || []).length
									? "<br><br>" +
									  __("Skipped {0}:", [m.skipped.length]) +
									  "<br>" +
									  m.skipped.join("<br>")
									: ""),
						});
						frm.reload_doc();
					})
			);
		}

		if (frm.doc.budget) {
			frm.add_custom_button(__("View Budget"), () =>
				frappe.set_route("Form", "Summer Flower Budget", frm.doc.budget)
			);
		}

		set_headline(frm);
		// A submitted plan never runs validate again, so its stored verdict is
		// frozen at approval. Ask for the live one.
		if (frm.doc.docstatus === 1) {
			frm.call({ doc: frm.doc, method: "get_protocol_freshness" }).then((r) => {
				const f = r.message || {};
				if (f.stale) {
					frm.dashboard.add_comment(f.note.replace(/\n/g, " "), "orange", true);
				}
			});
		}
	},

	protocol(frm) {
		// Changing the version does not change a single stored row until the plan is
		// rebuilt, so say so at the moment of the change rather than letting the old
		// numbers sit there under a new protocol name.
		if (frm.doc.protocol && frm.doc.plan_weeks && frm.doc.plan_weeks.length) {
			frm.dashboard.clear_headline();
			frappe.show_alert({
				message: __("Save, then Regenerate, to rebuild the plan on {0}.", [
					frm.doc.protocol,
				]),
				indicator: "orange",
			});
		}
	},
});

function set_headline(frm) {
	const parts = [];

	if (frm.doc.protocol_stale) {
		frm.dashboard.add_comment(
			__("These numbers were built on an older reading of {0}. Regenerate to apply it.", [
				frm.doc.protocol,
			]),
			"orange",
			true
		);
	}

	if (frm.doc.total_demand_stems) {
		parts.push(
			__("Coverage {0}%", [format_number(frm.doc.coverage_pct, null, 1)])
		);
	}
	if (frm.doc.weeks_in_deficit) {
		parts.push(
			__("{0} weeks in deficit, worst {1} stems", [
				frm.doc.weeks_in_deficit,
				format_number(frm.doc.worst_weekly_deficit, null, 0),
			])
		);
	}
	if (frm.doc.peak_weekly_sticking) {
		// This is the number that sizes the motherstock -- not the annual total.
		parts.push(
			__("Peak sticking {0} in {1}", [
				format_number(frm.doc.peak_weekly_sticking, null, 0),
				frm.doc.peak_sticking_week,
			])
		);
	}

	if (!parts.length) return;

	const indicator = frm.doc.weeks_in_deficit ? "orange" : "green";
	frm.dashboard.set_headline(parts.join(" &nbsp;·&nbsp; "), indicator);

	if (frm.doc.weeks_in_deficit) {
		frm.dashboard.add_comment(
			__(
				"Weekly variance swings hard because a whole block flushes in one week. Read the Monthly tab for the reliable picture."
			),
			"blue",
			true
		);
	}
}
