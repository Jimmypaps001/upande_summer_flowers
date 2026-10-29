// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// The "i" icon that runs a form's walkthrough.
//
// A Form Tour on its own is unreachable. Frappe builds frm.tour on every form but
// only ever starts it from an onboarding step, or when the tour happens to be named
// exactly after its doctype. Ours are named for what they explain, so nothing ever
// started them and the walkthrough written for the Crop Protocol had no way in.
// This puts the icon on the form and starts the named tour when it is clicked.
//
// The tours carry the wording that used to sit under every field as a description.
// Shown once, when asked for, instead of occupying the form for good.

frappe.provide("upande_summer_flowers.walkthrough");

// Whether each tour exists, asked once per page load rather than on every refresh.
upande_summer_flowers.walkthrough._exists = {};

upande_summer_flowers.walkthrough.attach = function (frm, tour_name) {
	if (!frm) return;
	// Every refresh, the form toolbar empties the icon group (make_menu ->
	// clear_icons), so the icon has to go back on each time it is missing. A flag
	// on frm is not enough: a draft refreshes again after load -- workflow
	// buttons, derived values, a save -- and the icon was wiped and never
	// re-added. Only approved, read-only protocols refreshed once and kept it.
	const cache = upande_summer_flowers.walkthrough._exists;
	if (cache[tour_name] === undefined) {
		cache[tour_name] = frappe.db.exists("Form Tour", tour_name);
	}

	Promise.resolve(cache[tour_name]).then((exists) => {
		// A site without the fixture is not an error. An icon that opens an empty
		// overlay would be, so nothing is added.
		if (!exists) return;
		// Checked after the await: two refreshes in flight would otherwise both add it.
		if (frm.page.icon_group.find(".sf-walkthrough-btn").length) return;
		frm.page.add_action_icon(
			"info",
			() => upande_summer_flowers.walkthrough.start(frm, tour_name),
			"sf-walkthrough-btn",
			__("How this form is filled in")
		);
	});
};

upande_summer_flowers.walkthrough.is_shown = function (frm, fieldname) {
	const field = frm.get_field(fieldname);
	if (!field || !field.$wrapper) return false;
	// The field's own depends_on, then its section's.
	if (field.$wrapper.hasClass("hide-control")) return false;
	return !field.$wrapper.closest(".form-section.hide-control, .form-section.empty-section").length;
};

upande_summer_flowers.walkthrough.start = function (frm, tour_name) {
	// A tour highlights fields one at a time, so init() opens every collapsed
	// section first -- a step pointing at a field inside a shut section highlights
	// nothing and looks broken.
	Promise.resolve(frm.tour.init({ tour_name: tour_name }))
		.then(() => {
			// Steps for fields the form is not showing are dropped -- a rose, or a
			// new protocol with no crop type yet, hides the summer flower fields, and
			// a step on a hidden field highlights nothing. Tabs do not count as
			// hidden: the tour switches tab itself.
			const steps = (frm.tour.tour.steps || []).filter((s) =>
				upande_summer_flowers.walkthrough.is_shown(frm, s.fieldname)
			);
			if (!steps.length) return;
			// The tour moves between tabs by idx, so it is renumbered to match.
			steps.forEach((s, i) => (s.idx = i + 1));
			frm.tour.tour.steps = steps;
			frm.tour.build_steps();
			frm.tour.update_driver_steps();
			frm.tour.start();
		})
		.catch((e) => {
			frappe.msgprint({
				title: __("Walkthrough"),
				message: __("The walkthrough could not be opened: {0}", [e.message || e]),
				indicator: "orange",
			});
		});
};
