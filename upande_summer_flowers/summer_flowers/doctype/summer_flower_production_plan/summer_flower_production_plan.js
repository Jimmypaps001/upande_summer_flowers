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

		render_sheet(frm);
		draw_calendar(frm);
		// Seven buttons across a toolbar is a list nobody reads. One dropdown,
		// in the order the work happens: plan, allocate, procure, plant.
		const ACTIONS = __("Actions");

		frm.add_custom_button(__("Download Planning Sheet"), () => {
			open_url_post(
				"/api/method/upande_summer_flowers.summer_flowers.plan_sheet.sheet_csv",
				{ plan: frm.doc.name },
				true
			);
		}, ACTIONS);
		frm.add_custom_button(__("Edit Crop Protocol"), () => {
			// The assumptions at the foot of the sheet are this protocol. Editing it
			// puts it back to Draft for re-approval, which then writes a new version
			// and this plan can be regenerated against it.
			frappe.set_route("Form", "Crop Protocol",
				`${frm.doc.variety}-${frm.doc.farm}`);
		}, ACTIONS);

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
			}, ACTIONS);
		}

		if (frm.doc.docstatus === 1) {
			// Where the plants come from is not a detail of the plan -- it sets the
			// lead time, and the lead time sets the order date. Asked once, here.
			frm.add_custom_button(__("Allocate Blocks"), () => allocate_blocks(frm),
				ACTIONS);
			// The line is decided before it is bought. Plan Motherstock works out
			// how many plantlets a line would need; Plan Procurement then orders
			// that figure rather than deriving its own.
			frm.add_custom_button(__("Plan Motherstock"), () => plan_motherstock(frm),
				ACTIONS);
			frm.add_custom_button(__("Plan Procurement"), () => source_plantlets(frm),
				ACTIONS);
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
					}),
				ACTIONS
			);
		}

		if (frm.doc.budget) {
			frm.add_custom_button(__("View Budget"), () =>
				frappe.set_route("Form", "Summer Flower Budget", frm.doc.budget),
				ACTIONS
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

function fmt(n) {
	if (n === "" || n === null || n === undefined) return "";
	if (!n) return "0";
	return frappe.format(n, { fieldtype: "Int" });
}

function render_sheet(frm) {
	const wrap = frm.get_field("matrix_html").$wrapper;
	wrap.html(`<div class="text-muted">${__("Loading sheet...")}</div>`);
	frappe.call({
		method: "upande_summer_flowers.summer_flowers.plan_sheet.sheet",
		args: { plan: frm.doc.name },
		callback: (r) => {
			if (!r.message) return;
			wrap.html(sheet_html(r.message));
		},
	});
}

function sheet_html(d) {
	const head = ["BLOCK", "Planting wk", "Planting Date", "Pinching Date",
		"NET AREA (m2)", "Uprooting Date", "NET AREA (Ha)", "NO OF BEDS", "NO.PLANTS"];
	const cols = d.grid.length;
	let h = `<div style="overflow-x:auto"><table class="table table-bordered table-sm"
		style="font-size:11px;white-space:nowrap">`;

	h += `<thead><tr><th colspan="${head.length}"></th>`;
	d.grid.forEach((g) => (h += `<th class="text-center">${g.year}</th>`));
	h += `<th></th><th></th></tr><tr>`;
	head.forEach((c) => (h += `<th>${c}</th>`));
	d.grid.forEach((g) => (h += `<th class="text-center">${g.week}</th>`));
	h += `<th>TOTAL</th><th>STEMS/<br>HA</th><th>LIFETIME</th></tr></thead><tbody>`;

	// A planting with no block produces nothing and has to look different from one
	// that produces nothing because its weeks are outside the season.
	d.plantings.forEach((p) => {
		const style = p.not_placed ? ' style="background:#fff5f5"' :
			(p.is_new ? "" : ' style="background:#f7f7f7"');
		h += `<tr${style}><td>${p.block}</td><td>${p.planting_week || ""}</td>
			<td>${p.planting_date}</td><td>${p.pinch_date}</td>
			<td class="text-right">${fmt(p.net_area_sqm)}</td><td>${p.uproot_date}</td>
			<td class="text-right">${((p.net_area_sqm || 0) / 10000).toFixed(4)}</td>
			<td class="text-right">${fmt(p.beds)}</td>
			<td class="text-right">${fmt(p.plants)}</td>`;
		p.weekly.forEach((v) => (h += `<td class="text-right">${v ? fmt(v) : ""}</td>`));
		h += `<td class="text-right"><b>${fmt(p.season_total)}</b></td>
			<td class="text-right">${fmt(Math.round(p.season_stems_per_ha))}</td>
			<td class="text-right">${fmt(p.lifetime_total)}</td></tr>`;
	});

	const t = d.totals;
	h += `<tr style="font-weight:bold"><td>TOTAL</td><td></td><td></td><td></td>
		<td class="text-right">${fmt(t.net_area_sqm)}</td><td></td>
		<td class="text-right">${((t.net_area_sqm || 0) / 10000).toFixed(4)}</td>
		<td class="text-right">${fmt(t.beds)}</td>
		<td class="text-right">${fmt(t.plants)}</td>
		<td colspan="${cols}"></td>
		<td class="text-right">${fmt(t.season)}</td>
		<td class="text-right">${fmt(Math.round(t.season_stems_per_ha))}</td>
		<td class="text-right">${fmt(t.lifetime)}</td></tr>`;

	const band = (label, values, opts = {}) => {
		let row = `<tr${opts.style || ""}><td colspan="${head.length}">${label}</td>`;
		for (let i = 0; i < cols; i++) {
			const v = values[i];
			const neg = opts.signed && v < 0;
			row += `<td class="text-right"${neg ? ' style="color:#c0392b"' : ""}>${
				v === 0 && opts.blankZero ? "" : fmt(v)
			}</td>`;
		}
		return row + `<td colspan="3"></td></tr>`;
	};

	h += band(__("Weekly production"), d.weekly_production, { style: ' style="font-weight:bold"' });
	h += band(__("Monthly production"), d.monthly_production, { blankZero: true });
	d.grades.forEach((g) => {
		h += g.weekly.length
			? band(`${g.grade} (${g.pct}%)`, g.weekly)
			: `<tr><td colspan="${head.length}">${g.grade}</td>
				<td colspan="${cols + 3}" class="text-muted">${
					__("No allocation % on the protocol, so this grade cannot be split out.")
				}</td></tr>`;
	});
	h += band(__("Weekly market demand"), d.weekly_demand);
	h += band(__("Monthly market demand"), d.monthly_demand, { blankZero: true });
	h += band(__("DIFFERENCE"), d.difference, { signed: true, style: ' style="font-weight:bold"' });
	h += band(__("Area (ha standing)"), d.area.map((v) => Math.round(v * 10000) / 10000));
	h += `</tbody></table></div>`;

	const a = d.assumptions || {};
	if (a.flush_schedule) {
		h += `<div style="margin-top:12px"><b>${
			__("{0} assumptions", [d.variety])
		}</b> <span class="text-muted">${
			__("from Crop Protocol {0} -- change them there", [d.protocol])
		}</span>
		<table class="table table-bordered table-sm" style="font-size:11px;max-width:760px">
		<tr><td>${__("m² per bed")}</td>
			<td class="text-right">${a.net_sqm_per_bed}</td><td></td></tr>
		<tr><td>${__("Plants per m² (net) / per bed")}</td><td class="text-right">${
			a.plants_per_sqm_net} / ${fmt(a.plants_per_bed)}</td>
			<td class="text-muted">${fmt(a.plants_per_net_ha)} ${__("per hectare")}</td></tr>
		<tr><td>${__("Pinch at week / total weeks in ground")}</td>
			<td class="text-right">${a.pinch_at_week} / ${a.total_weeks_in_ground}</td>
			<td class="text-muted">${(a.flushes_per_year || 0).toFixed(2)} ${
			__("flushes per year")}</td></tr>
		</table>
		<table class="table table-bordered table-sm" style="font-size:11px;max-width:760px">
		<thead><tr><th>${__("Flush")}</th><th>${__("Weeks from pinch")}</th>
			<th>${__("Weeks from planting")}</th><th class="text-right">${
			__("Stems/plant")}</th><th class="text-right">${
			__("Stems/ha/yr")}</th></tr></thead><tbody>`;
		a.flush_schedule.forEach((f) => {
			h += `<tr><td>${f.flush}</td><td>${f.weeks_from_pinch}</td>
				<td>${f.weeks_from_planting}</td>
				<td class="text-right">${f.stems_per_plant}</td>
				<td class="text-right">${fmt(Math.round(f.stems_per_ha_year))}</td></tr>`;
		});
		h += `<tr style="font-weight:bold"><td colspan="3">${
			__("Total stems per plant life")}</td>
			<td class="text-right">${a.total_stems_per_plant_life}</td>
			<td class="text-right">${fmt(Math.round(a.stems_per_net_ha_life))}</td></tr>
			<tr><td colspan="3">${__("Per year")}</td><td></td>
			<td class="text-right">${fmt(Math.round(a.stems_per_net_ha_year))}</td></tr>
			<tr><td colspan="3">${__("Best year")}</td>
			<td class="text-right">${a.best_year_stems_per_plant}</td>
			<td class="text-right">${fmt(Math.round(a.best_year_stems_per_net_ha))}</td></tr>
			</tbody></table></div>`;
	}
	return h;
}

function draw_calendar(frm) {
	const field = frm.get_field("calendar_html");
	if (!field || !window.sf_calendar) return;
	const int = (n) => frappe.format(n || 0, { fieldtype: "Int" });

	// Variance rather than production, because the question a calendar answers is
	// which weeks are short -- and a signed scale can say that in colour.
	const weeks = (frm.doc.plan_weeks || []).map((w) => ({
		date: w.week_start_date,
		value: w.variance_stems,
		title: `${w.year}-W${String(w.week_no).padStart(2, "0")}: demand ${
			int(w.demand_stems)}, production ${int(w.production_stems)}, variance ${
			int(w.variance_stems)}`,
	}));

	// A planting is four dated moments, not one, and they are weeks apart: the day it
	// is stuck, the day it goes in the ground, the day it is pinched and the day it
	// first cuts. Reading them off a table meant counting weeks by hand.
	const events = [];
	(frm.doc.plan_blocks || []).forEach((b) => {
		const where = b.block || __("no block yet");
		if (b.planting_date) {
			events.push({ date: b.planting_date, colour: b.not_placed ? "#c0392b" : "#2980b9",
				title: `${__("Plant")} ${int(b.beds)} ${__("beds")} — ${where}` });
		}
		if (b.pinch_date) {
			events.push({ date: b.pinch_date, colour: "#f39c12",
				title: `${__("Pinch")} — ${where}` });
		}
	});
	if (frm.doc.tc_order_by_date) {
		events.push({ date: frm.doc.tc_order_by_date, colour: "#8e44ad",
			title: `${__("TC order deadline")}: ${int(frm.doc.tc_plants_to_order)} ${
				__("plantlets")}` });
	}

	window.sf_calendar(field, {
		weeks, events, signed: true,
		legend: [{ dot: "#2980b9", label: __("planting") },
			{ dot: "#c0392b", label: __("planting with no block") },
			{ dot: "#f39c12", label: __("pinch") },
			{ dot: "#8e44ad", label: __("TC order deadline") },
			{ label: __("red weeks are short of demand, green weeks are over") }],
		empty: __("No weeks yet. Regenerate the plan."),
	});
}


// ---------------------------------------------------------------- sourcing
// Pick how the material is got; everything else follows from it.
function source_plantlets(frm) {
	const M = "upande_summer_flowers.summer_flowers.doctype"
		+ ".summer_flower_procurement_plan.summer_flower_procurement_plan.";
	frappe.call({ method: M + "methods_for", args: { production_plan: frm.doc.name },
		freeze: true }).then((r) => {
		const s = r.message;
		if (!s) return;
		const stages = (s.options || []).map((o) => o.stage);
		const describe = (stage) => {
			const o = (s.options || []).find((x) => x.stage === stage);
			if (!o) return "";
			const notice = (o.weeks_to_ground || 0) + (o.lead_weeks || 0);
			return __("{0} weeks from ordering to a plant in the ground ({1} raising it, {2} supplier lead). One unit becomes {3} plants.",
				[notice, o.weeks_to_ground, o.lead_weeks, (o.plants_per_unit || 0).toFixed(2)]);
		};
		const space = s.beds_available
			? __("{0} beds available — {1}", [s.beds_available, s.space_basis])
			: __("No ground recorded at this farm, so the order cannot be trimmed to fit it.");

		// The protocol already answers this. Buying and propagating are not two
		// options to pick between: the purchase is where the route starts and
		// propagation is every stage between there and the ground, so a crop can
		// want both. Defaulting to Purchase at whichever stage came first was
		// wrong in both fields at once on every crop the farm propagates.
		const dec = s.decided || {};
		const buyable = (dec.buyable || []).length ? dec.buyable : stages;

		// Nothing can be built from this protocol, and build() will say so the
		// moment Generate is pressed. Offering the button anyway made the reader
		// pick a supplier, tick a box and press it to be told the protocol was
		// never going to allow any of it. Say it once, and offer the fix.
		if (!dec.method) {
			const st = s.stale_version;
			// If the route was added since this plan was built, the answer is to
			// regenerate, not to edit the protocol again.
			const why = (st && st.newer_has_route)
				? __("{0} has no material route. {1} is the current version and does have one — point this plan at it and Regenerate.",
					[st.plan_version, st.current_version])
				: (dec.reason
					|| __("This protocol does not say how {0}'s material is got.", [s.variety]));
			const fixOnProtocol = !(st && st.newer_has_route);
			const blocked = new frappe.ui.Dialog({
				title: __("Nothing to procure yet"),
				fields: [{ fieldname: "why", fieldtype: "HTML",
					options: `<p>${frappe.utils.escape_html(why)}</p>` +
						(fixOnProtocol
							? `<p class="text-muted small">${frappe.utils.escape_html(
								__("The material route is on the Crop Protocol. Mark the stage the "
								 + "material is bought at, approve it, then regenerate this plan."))}</p>`
							: "") }],
				primary_action_label: fixOnProtocol
					? __("Add the route now") : __("Regenerate this plan"),
				primary_action() {
					blocked.hide();
					if (!fixOnProtocol) {
						frm.call({ doc: frm.doc, method: "regenerate", freeze: true,
							freeze_message: __("Regenerating...") })
							.then(() => frm.reload_doc());
						return;
					}
					// Taken to the table to fill in, not dropped at the top of a long
					// protocol to find it. The docname is followed off the version's
					// own link, so this cannot route to a form that is not there.
					if (!s.crop_protocol) {
						frappe.msgprint({
							title: __("No Crop Protocol to open"),
							indicator: "red",
							message: __("{0} does not link back to a Crop Protocol, so the "
								+ "route cannot be added from here. Find the protocol for "
								+ "{1} at {2} and add it there.",
								[s.protocol, s.variety, s.farm]),
						});
						return;
					}
					const target = s.crop_protocol, field = s.route_fieldname;
					frappe.set_route("Form", "Crop Protocol", target).then(() => {
						// set_route resolves before the form has finished drawing, and a
						// tab switched on a half-built form is undone by the refresh that
						// follows. So wait for the control to exist, switch, and check it
						// took -- the route lives on its own tab, and landing on Details
						// leaves the reader hunting for the table they were sent to fill in.
						let tries = 0;
						const land = () => {
							const ctl = cur_frm && cur_frm.doc && cur_frm.doc.name === target
								&& cur_frm.fields_dict && cur_frm.fields_dict[field];
							if (!ctl) {
								if (tries++ < 40) setTimeout(land, 150);
								return;
							}
							if (ctl.tab && ctl.tab.set_active) ctl.tab.set_active();
							cur_frm.scroll_to_field(field);
							// Settled only when the table is really on screen; a refresh can
							// still put Details back under it.
							if (ctl.$wrapper[0].offsetParent === null && tries++ < 40) {
								setTimeout(land, 150);
								return;
							}
							frappe.show_alert({
								indicator: "blue",
								message: __("Add the stages here, tick the one the material "
									+ "is bought at, then approve and regenerate {0}.",
									[frm.doc.name]),
							}, 12);
						};
						land();
					});
				},
			});
			blocked.show();
			return;
		}

		// A plan already sourcing this one is not a problem and not a warning. It is
		// the document about to be revised, so it is named and left at that. Only
		// the one case that is actually refused -- the order has gone out -- is red.
		const had = s.existing;
		const blocked = had && had.docstatus === 1 && had.order_date_passed;
		const verdict =
			(had
				? (blocked
					? `<p style="color:var(--red-600,#c0392b)">${
						frappe.utils.escape_html(had.why_not)}</p>`
					: `<p class="text-muted">${frappe.utils.escape_html(
						__("Revising {0} ({1} {2}). Its lines are worked out again "
						   + "from the choices below; it keeps its number and history.",
							[had.name, format_number(had.units || 0, null, 0),
							 had.entry_stage || __("units")]))}</p>`)
				: "") +
			(dec.reason
				? `<p class="text-muted small">${frappe.utils.escape_html(dec.reason)}</p>`
				: "");

		const d = new frappe.ui.Dialog({
			title: __("How is the plant material got?"),
			fields: [
				{ fieldname: "verdict", fieldtype: "HTML", options: verdict },
				{ fieldname: "method", label: __("Method"), fieldtype: "Select", reqd: 1,
				  options: ["Purchase", "Propagate"], default: dec.method || "Purchase",
				  read_only: 1,
				  description: __("Read off the material route on {0}. Change it there, not here.",
					[s.protocol]) },
				{ fieldname: "propagation_note", fieldtype: "HTML",
				  options: dec.propagates
					? `<p class="text-muted small">${frappe.utils.escape_html(
						__("A propagation plan will be raised as well: {0} is raised here through {1}.",
							[s.variety, (dec.in_house || []).join(", ")]))}</p>`
					: "" },
				{ fieldname: "entry_stage", label: __("Bought as"), fieldtype: "Select",
				  options: buyable, default: dec.entry_stage || buyable[0],
				  depends_on: "eval:doc.method=='Purchase'",
				  description: buyable.length > 1
					? __("The protocol marks more than one stage as bought, so this is the one thing left to choose.")
					: "" },
				{ fieldname: "stage_note", fieldtype: "HTML" },
				{ fieldname: "supplier", label: __("Supplier"), fieldtype: "Link",
				  options: "Supplier", depends_on: "eval:doc.method=='Purchase'" },
				// Only a route through a pool has any of this to decide. A crop
				// bought as rooted plants buys one per plant and there is nothing
				// to choose.
				{ fieldname: "tc_break", fieldtype: "Section Break",
				  label: __("Propagation"), hidden: 1 },
				{ fieldname: "tc_working", fieldtype: "HTML" },
				{ fieldname: "tc_divert", label: __("Multiply"),
				  fieldtype: "Int",
				  description: __("Times. Worked out from the peak week and the "
					+ "line's life, not chosen: each multiplication halves, thirds "
					+ "or quarters the order, and costs one establishment off the "
					+ "weeks the pool has left to cut. Change it to see what it "
					+ "does.") },
				{ fieldname: "tc_qty", label: __("Plantlets to order"),
				  fieldtype: "Int",
				  description: __("What the peak week asks for at that "
					+ "multiplication, rounded up to a whole planting area. Type "
					+ "another figure to override it.") },
				{ fieldname: "tc_table", fieldtype: "HTML" },
				{ fieldname: "space_break", fieldtype: "Section Break", label: __("Ground") },
				{ fieldname: "space_note", fieldtype: "HTML",
				  options: `<p class="text-muted">${frappe.utils.escape_html(space)}</p>` },
				{ fieldname: "fit_to_space", label: __("Only order what the ground can take"),
				  fieldtype: "Check", default: s.beds_available ? 1 : 0,
				  read_only: s.beds_available ? 0 : 1,
				  description: __("The demand is not reduced by this. It says what can be planted, not what is wanted.") },
			],
			// "Update", not "Replace": the existing plan is revised in place rather
			// than deleted and rewritten, so there is nothing to confirm losing. The
			// confirmation that used to sit here cost the reader a second decision
			// to get to the only thing they came for.
			primary_action_label: s.existing
				? __("Update {0}", [s.existing.name]) : __("Generate"),
			primary_action(values) {
				// The dialog used to hide itself before the call. A server refusal --
				// "SFPROC-… already sources this plan" is the common one -- then had
				// no dialog to appear over and went nowhere at all: the button did
				// nothing, said nothing, and the reader pressed it again. It stays up
				// until there is something to show for it.
				if (s.existing && s.existing.docstatus === 1
					&& s.existing.order_date_passed) {
					frappe.msgprint({ title: __("The order has already gone out"),
						indicator: "red", message: s.existing.why_not });
					return;
				}
				d.get_primary_btn().prop("disabled", true);
				frappe.call({ method: M + "build", freeze: true,
					freeze_message: __("Working out the orders..."),
					args: { production_plan: frm.doc.name, method: values.method,
						entry_stage: values.entry_stage, supplier: values.supplier,
						fit_to_space: values.fit_to_space ? 1 : 0,
						cycles: d.get_value("tc_divert"),
						tc_qty: d.get_value("tc_qty") || null,
						replace: 1 } })
					.then((res) => {
						if (!res || !res.message) return;
						d.hide();
						frappe.set_route("Form", "Summer Flower Procurement Plan",
							res.message);
					})
					.catch(() => {
						// Frappe has already shown the server's own message, and the
						// one collision that used to land here is now settled before
						// the form is drawn.
					})
					.always(() => d.get_primary_btn().prop("disabled", false));
			},
		});
		const note = () => d.fields_dict.stage_note.$wrapper.html(
			`<p class="text-muted small">${frappe.utils.escape_html(describe(d.get_value("entry_stage")))}</p>`);
		d.fields_dict.entry_stage.df.onchange = note;
		d.show();
		note();
		sf_propagation_section(d, frm);
		// The no-route case never reaches here: it is refused above, before a form
		// the reader cannot use is put in front of them.
		if (s.stale_version) {
			d.set_df_property("entry_stage", "description",
				__("Dated from {0}. {1} is now the current version; regenerate the plan to use it.",
					[s.stale_version.plan_version, s.stale_version.current_version]));
		}
	});
}


// ------------------------------------------------------------- allocation
// Which block a planting goes in is a decision. The planner ranks what fits and
// what nearly fits; the grower knows which house suits the crop.
function allocate_blocks(frm) {
	const M = "upande_summer_flowers.summer_flowers.doctype"
		+ ".summer_flower_production_plan.summer_flower_production_plan.";
	frappe.call({ method: M + "allocation_options", args: { plan: frm.doc.name },
		freeze: true }).then((r) => {
		const s = r.message;
		if (!s || !s.rows.length) {
			frappe.msgprint(__("This plan has no new plantings to allocate."));
			return;
		}
		sf_block_picker(frm, s, M);
	});
}

// Which block a planting goes in is a decision, and the planner has already
// worked out everything needed to make it: how many beds each block has free for
// that whole period, which ones only nearly fit, when they free up and what is
// holding them. A dropdown of block names threw all of that away and made the
// reader open another screen to find it. So the blocks are shown, not listed.
function sf_block_picker(frm, s, M) {
	const chosen = {};
	s.rows.forEach((row) => { chosen[row.row] = row.block || row.suggested || ""; });

	const esc = frappe.utils.escape_html;
	// Not frappe.format(.., {fieldtype: "Int"}) -- that returns a right-aligned
	// <div>. Escaped into a chip it printed the markup as text and broke "40/40"
	// across three lines. A number here is a number.
	const int = (n) => format_number(n || 0, null, 0);
	const shortDate = (d) => frappe.datetime.str_to_user(d);

	// "Torongo GH18 - KR - Block 11B" is one block code and a lot of repetition.
	// The house is the same for every chip in the dialog and the word "Block" is
	// on all of them, so neither tells the reader anything; the code does.
	const code = (name) => (name || "").split(" - ").pop().replace(/^Block\s+/i, "");

	const chipFor = (row, c) => {
		const on = chosen[row.row] === c.block;
		const cls = ["sfa-chip", c.fits ? "fits" : (c.free_from ? "late" : "no"),
			on ? "on" : ""].join(" ");
		// A block is its beds. Free-of-total says at a glance whether it is tight,
		// and a block that only nearly fits says when and who is in the way --
		// moving a planting a fortnight is usually cheaper than finding land.
		const bits = [`<b>${esc(code(c.block))}</b>`,
			`<span class="sfa-beds">${int(c.free_beds)}/${int(c.total_beds)}</span>`];
		let sub;
		if (c.fits) {
			sub = __("{0} spare", [int(c.free_beds - row.beds)]);
		} else if (c.free_from) {
			sub = __("free {0} · {1}w late", [shortDate(c.free_from), c.weeks_late]);
		} else {
			sub = __("no room");
		}
		// What is on the block, and what was on it before. A chip that only said
		// "52 free" told a grower nothing about whether this is the right ground:
		// the same crop back on the same beds is a decision, not a detail.
		const onBlock = c.standing || [];
		const hist = (c.history || []).filter(
			(h) => !onBlock.some((x) => x.variety === h.variety));
		const held = onBlock.map((b) =>
			__("{0} — {1} holds {2} beds to {3}", [b.variety || "?", b.planting,
				int(b.beds), shortDate(b.frees_on)]))
			.concat(hist.map((h) =>
				__("was {0}, planted {1}", [h.variety || "?", shortDate(h.planted)])))
			.join("\n");
		const now = onBlock.length
			? onBlock.map((b) => esc(b.variety || "?")).filter(
				(v, i, a) => a.indexOf(v) === i).join(", ")
			: "";
		const was = hist.length ? esc(hist[0].variety || "") : "";
		// A block that is held is a decision, not a dead end: taking the crop
		// standing on it out a fortnight early is usually cheaper than finding land.
		// Offered here, where the clash is seen, rather than discovered at submit.
		const blocker = (c.blockers || [])[0];
		const up = (!c.fits && blocker && !c.free_from) || (!c.fits && blocker)
			? `<span class="sfa-up" data-uproot="${esc(blocker.planting)}" ` +
			  `data-on="${esc(row.planting_date)}" ` +
			  `title="${esc(__("Take {0} out on {1} so this block is free",
				[blocker.planting, shortDate(row.planting_date)]))}">` +
			  `${__("uproot")}</span>`
			: "";
		return `<button type="button" class="${cls}" data-row="${esc(row.row)}" ` +
			`data-block="${esc(c.block)}" title="${esc(held || c.block)}">` +
			`<span class="sfa-top">${bits.join(" ")}</span>` +
			`<span class="sfa-sub">${esc(sub)}${up}</span>` +
			(now ? `<span class="sfa-on">${__("now")}: ${now}</span>` : "") +
			(was ? `<span class="sfa-was">${__("was")}: ${was}</span>` : "") +
			`</button>`;
	};

	const rowHtml = (row) => {
		const fits = row.candidates.filter((c) => c.fits).length;
		const state = chosen[row.row]
			? `<span class="sfa-ok">${esc(code(chosen[row.row]))}</span>`
			: `<span class="sfa-none">${__("no block")}</span>`;
		return `<div class="sfa-row${chosen[row.row] ? "" : " unset"}" data-rowid="${esc(row.row)}">` +
			`<div class="sfa-head">` +
				`<span class="sfa-wk">${esc(row.planting_week)}</span>` +
				`<span class="sfa-meta">${shortDate(row.planting_date)} · ` +
					`${int(row.beds)} ${__("beds")} · ${int(row.plants)} ${__("plants")}</span>` +
				state +
			`</div>` +
			`<div class="sfa-chips">` +
				(row.candidates.length
					? row.candidates.map((c) => chipFor(row, c)).join("") +
					  `<button type="button" class="sfa-chip clear" data-row="${esc(row.row)}" ` +
					  `data-block="">${__("none")}</button>`
					: `<span class="sfa-empty">${__("No block at {0} has {1} beds free for that whole period.",
						[esc(s.farm), int(row.beds)])}</span>`) +
			`</div></div>`;
	};

	const summary = () => {
		const set = s.rows.filter((r) => chosen[r.row]).length;
		const none = s.rows.length - set;
		return `<div class="sfa-sum">${int(set)} ${__("of")} ${int(s.rows.length)} ` +
			`${__("placed")}` +
			(none ? ` · <span class="sfa-none">${int(none)} ${__("without a block")}</span>` : "") +
			`</div>`;
	};

	const d = new frappe.ui.Dialog({
		title: __("Allocate blocks — {0} at {1}", [s.variety, s.farm]),
		size: "extra-large",
		fields: [{ fieldname: "picker", fieldtype: "HTML" }],
		primary_action_label: __("Assign"),
		primary_action() {
			d.hide();
			frappe.call({ method: M + "allocate", freeze: true,
				freeze_message: __("Assigning blocks…"),
				args: { plan: frm.doc.name, assignments: JSON.stringify(chosen) } })
				.then((res) => {
					const m = res.message || {};
					frappe.show_alert({ indicator: m.still_unplaced ? "orange" : "green",
						message: __("{0} allocated, {1} still without a block",
							[m.assigned, m.still_unplaced]) });
					frm.reload_doc();
				});
		},
		secondary_action_label: __("Use every suggestion"),
		secondary_action() {
			s.rows.forEach((row) => {
				chosen[row.row] = row.block || row.suggested || "";
			});
			paint();
		},
	});

	const paint = () => {
		d.fields_dict.picker.$wrapper.html(
			SF_ALLOC_CSS + summary() +
			`<div class="sfa-list">${s.rows.map(rowHtml).join("")}</div>`);
	};

	d.fields_dict.picker.$wrapper.on("click", ".sfa-up", function (e) {
		e.stopPropagation();
		const holder = this.dataset.uproot, on = this.dataset.on;
		frappe.confirm(
			__("Take {0} out on {1}? Its remaining flushes are given up, and the "
			   + "block is free from that day.", [holder, shortDate(on)]),
			() => frappe.call({ method: M + "uproot_to_free", freeze: true,
				args: { planting: holder, on_date: on },
				freeze_message: __("Uprooting...") })
				.then((r) => {
					const m = r.message || {};
					frappe.show_alert({ indicator: "orange",
						message: __("{0} out {1} weeks early — {2} stems given up.",
							[m.planting, m.weeks_early, int(m.stems_lost)]) }, 10);
					d.hide();
					allocate_blocks(frm);
				}));
	});

	d.fields_dict.picker.$wrapper.on("click", ".sfa-chip", function () {
		const rowid = this.dataset.row;
		// Clicking the block already chosen clears it, so a row can be emptied
		// without hunting for a "none" at the end of a long list of blocks.
		chosen[rowid] = (chosen[rowid] === this.dataset.block) ? "" : this.dataset.block;
		paint();
	});

	paint();
	d.show();
}

const SF_ALLOC_CSS = `<style>
.sfa-sum{font-size:.82rem;color:var(--text-muted);margin:0 0 10px}
.sfa-list{display:flex;flex-direction:column;gap:8px;max-height:60vh;overflow:auto}
.sfa-row{border:1px solid var(--border-color);border-radius:8px;padding:9px 11px;
  background:var(--card-bg)}
.sfa-row.unset{border-color:var(--orange-300, #f0b37e)}
.sfa-head{display:flex;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:7px}
.sfa-wk{font-weight:600;font-variant-numeric:tabular-nums}
.sfa-meta{font-size:.78rem;color:var(--text-muted);flex:1}
.sfa-ok{font-size:.78rem;font-weight:600;color:var(--green-600, #22863a)}
.sfa-none{font-size:.78rem;color:var(--orange-600, #b35309)}
.sfa-chips{display:flex;flex-wrap:wrap;gap:6px}
.sfa-chip{display:flex;flex-direction:column;align-items:flex-start;gap:1px;
  border:1px solid var(--border-color);background:var(--bg-color);
  border-radius:7px;padding:4px 9px;cursor:pointer;line-height:1.25;
  font-size:.78rem;text-align:left;min-width:76px}
.sfa-chip:hover{border-color:var(--gray-500, #8d99a6)}
.sfa-chip .sfa-beds{font-variant-numeric:tabular-nums;color:var(--text-muted);
  margin-left:5px}
.sfa-chip .sfa-sub{font-size:.7rem;color:var(--text-muted)}
.sfa-chip .sfa-on,.sfa-chip .sfa-was{display:block;font-size:.66rem;line-height:1.3;
  max-width:190px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.sfa-chip .sfa-on{color:var(--orange-600,#b35309)}
.sfa-chip .sfa-was{color:var(--text-muted);opacity:.85}
.sfa-chip.fits{border-left:3px solid var(--green-500, #2e7d32)}
.sfa-chip.late{border-left:3px solid var(--orange-500, #d9822b)}
.sfa-chip.no{border-left:3px solid var(--gray-400, #b8c2cc);opacity:.65}
.sfa-chip.on{background:var(--control-bg, #ecf1f5);border-color:var(--gray-700, #4c5a67);
  box-shadow:inset 0 0 0 1px var(--gray-700, #4c5a67)}
.sfa-chip.on .sfa-sub,.sfa-chip.on .sfa-beds{color:var(--text-color)}
.sfa-chip.clear{min-width:0;border-left:3px solid transparent;color:var(--text-muted)}
.sfa-empty{font-size:.78rem;color:var(--orange-600, #b35309)}
.sfa-up{margin-left:6px;padding:0 5px;border-radius:4px;font-size:.66rem;
  text-transform:uppercase;letter-spacing:.4px;cursor:pointer;
  border:1px solid var(--orange-500, #d9822b);color:var(--orange-600, #b35309)}
.sfa-up:hover{background:var(--orange-500, #d9822b);color:#fff}
</style>`;


// ---------------------------------------------------------- tissue culture
// Tissue culture is bought ONCE, multiplied if the farm wants to, and cut from
// until the line expires. So the quantity is not "one per plant" -- it is the
// pool the busiest week needs, divided by what the multiplication turns one
// plantlet into. That division is the whole decision, and it was happening
// invisibly inside build(): the reader saw a number and no way to move it.
// ------------------------------------------------------- propagation decision
// Two knobs, and they trade against each other: how many plantlets to buy, and
// how many weeks to send the whole cut back before any of it goes to the field.
// Buy fewer and you divert longer, which means ordering earlier. Everything else
// on this panel is a consequence of those two and the protocol, so none of it is
// editable.
//
// It used to ask for "multiplication cycles", which is not a thing anyone can
// choose: a mother cuts every week, so generations overlap week by week rather
// than queueing up one establishment apart. The number that replaced it is the
// one the propagation unit actually acts on.
function sf_propagation_section(d, frm) {
	const M = "upande_summer_flowers.summer_flowers.propagation_solver.";
	const int = (n) => format_number(n || 0, null, 0);
	const esc = frappe.utils.escape_html;
	const day = (x) => (x ? esc(frappe.datetime.str_to_user(x)) : "—");
	let busy = false;
	let last = { tc: null, divert: null };

	// An Int box showing nothing is worse than one showing a figure you disagree
	// with, and zero multiplications is a real answer, not an empty one -- so the
	// value goes at the model and at the input, and 0 is written as "0".
	const seed = (field, value) => {
		const f = d.fields_dict[field];
		if (!f) return;
		const n = cint(value);
		f.set_value(n);
		if (f.$input) f.$input.val(String(n));
	};

	const line = (k, v, note) =>
		`<tr><td class="text-muted" style="padding:2px 12px 2px 0;white-space:nowrap">${esc(k)}</td>` +
		`<td style="padding:2px 0"><b>${v}</b>${
			note ? ` <span class="text-muted">${esc(note)}</span>` : ""}</td></tr>`;

	// A generation is not a date. A mother cuts every week, so its cuttings are
	// stuck week after week and come online a week apart -- "gen 2 arrives" is a
	// run of weeks, and the arrivals column is how many.
	const gen_table = (c) => {
		const g = (c && c.generations) || [];
		if (!g.length) return "";
		return `<div style="margin-top:12px"><div class="text-muted small" ` +
			`style="text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">` +
			`${__("Generations")}</div>` +
			`<table class="table table-bordered" style="font-size:.78rem;margin:0">` +
			`<thead><tr><th>${__("Gen")}</th><th class="text-right">${__("Mothers")}</th>` +
			`<th class="text-right">${__("Arrivals")}</th><th>${__("Starts cutting")}</th>` +
			`<th class="text-right">${__("Cutting weeks")}</th><th>${__("Cleared")}</th>` +
			`</tr></thead><tbody>` +
			g.map((r) => `<tr><td>G${r.generation}</td>` +
				`<td class="text-right">${int(r.mothers)}</td>` +
				`<td class="text-right">${int(r.arrivals)}</td>` +
				`<td>${day(r.first_cut_date)}</td>` +
				`<td class="text-right">${int(r.cutting_weeks)}</td>` +
				`<td>${day(r.expiry_date)}</td></tr>`).join("") +
			`</tbody></table>` +
			`<p class="text-muted small">${esc(__(
				"Every generation is cleared on the same day, so one raised late gets "
				+ "fewer cutting weeks rather than a life of its own."))}</p></div>`;
	};

	// The whole line, week by week. This is the thing to read: a quantity is a
	// by-product of it, not the other way round.
	const week_table = (c) => {
		const t = (c && c.weeks_table) || [];
		if (!t.length) return "";
		const row = (r) => {
			const hot = r.shortfall > 0;
			return `<tr${r.event ? ' style="font-weight:600"' : ""}>` +
				`<td>${r.week_no}</td><td>${day(r.week_start)}</td>` +
				`<td>${esc(r.generations_live || "")}</td>` +
				`<td class="text-right">${int(r.mothers_standing)}</td>` +
				`<td class="text-right">${int(r.cuttings_cut)}</td>` +
				`<td class="text-right">${r.to_multiplication ? int(r.to_multiplication) : ""}</td>` +
				`<td class="text-right">${r.to_field ? int(r.to_field) : ""}</td>` +
				`<td class="text-right">${r.demand ? int(r.demand) : ""}</td>` +
				`<td class="text-right"${hot ? ' style="color:var(--red-600,#c0392b)"' : ""}>` +
				`${hot ? int(r.shortfall) : ""}</td>` +
				`<td>${esc(r.event || "")}</td></tr>`;
		};
		return `<div style="margin-top:12px"><div class="text-muted small" ` +
			`style="text-transform:uppercase;letter-spacing:.05em;margin-bottom:4px">` +
			`${__("Week by week")} <span style="text-transform:none">(${t.length} ${__("weeks")})</span></div>` +
			`<div style="max-height:320px;overflow:auto;border:1px solid var(--border-color,#e2e4e9);border-radius:6px">` +
			`<table class="table table-bordered" style="font-size:.74rem;margin:0">` +
			`<thead style="position:sticky;top:0;background:var(--card-bg,#fff);z-index:1">` +
			`<tr><th>${__("Wk")}</th><th>${__("Week of")}</th><th>${__("Standing")}</th>` +
			`<th class="text-right">${__("Mothers")}</th><th class="text-right">${__("Cut")}</th>` +
			`<th class="text-right">${__("To mult.")}</th><th class="text-right">${__("To field")}</th>` +
			`<th class="text-right">${__("Needs")}</th><th class="text-right">${__("Short")}</th>` +
			`<th>${__("Event")}</th></tr></thead><tbody>` +
			t.map(row).join("") + `</tbody></table></div></div>`;
	};

	const show = (o) => {
		const on = !!(o && o.propagates);
		d.set_df_property("tc_break", "hidden", on ? 0 : 1);
		["tc_working", "tc_qty", "tc_divert", "tc_table"].forEach((f) =>
			d.set_df_property(f, "hidden", on ? 0 : 1));
		if (!on) return;
		const gens = d.fields_dict.tc_table.$wrapper;
		if (!o.solvable) {
			d.fields_dict.tc_working.$wrapper.html(
				`<p class="small" style="color:var(--red-600,#c0392b)">${
					esc(o.reason || __("No order covers this plan."))}</p>`);
			gens.html("");
			return;
		}

		const z = o.sizing, c = o.schedule, need = o.need || {};
		const qty = d.fields_dict.tc_qty, mult = d.fields_dict.tc_divert;
		if (qty && document.activeElement !== qty.$input?.[0]) qty.$input?.val(c.tc);
		if (mult && document.activeElement !== mult.$input?.[0]) {
			mult.$input?.val(c.cycles);
		}

		// The peak week is the whole argument: the pool is bought once and cut from
		// every week, so the busiest week sizes it and the rest lend it nothing.
		d.fields_dict.tc_working.$wrapper.html(
			`<p class="small" style="margin-bottom:6px">` +
			`<b>${int(need.plants)}</b> ${__("plants")} · ` +
			`<b>${int(need.cuttings)}</b> ${__("cuttings")} · ` +
			`${__("sticking")} <b>${day(z.first_sticking)}</b> ${__("to")} ` +
			`<b>${day(z.last_sticking)}</b></p>` +
			`<p class="small" style="margin-bottom:2px">` +
			__("The busiest week is {0} at {1} cuttings. That is what the pool is "
			   + "sized for — cuttings cannot be banked, so the weeks either side "
			   + "lend it nothing.",
				[z.peak_week_label, int(z.peak_cuttings)]) + `</p>` +
			`<p class="small">` +
			__("Multiplying {0} time(s) is suggested: {1} plantlets.",
				[z.suggested, int((z.options[z.suggested] || {}).buy)]) + `</p>`);

		const line = (k, v, note) =>
			`<tr><td class="text-muted" style="padding:2px 12px 2px 0;white-space:nowrap">${esc(k)}</td>` +
			`<td style="padding:2px 0"><b>${v}</b>${
				note ? ` <span class="text-muted">${esc(note)}</span>` : ""}</td></tr>`;

		let rows = "";
		rows += line(__("Peak week"), `${esc(z.peak_week_label)}`,
			__("{0} cuttings", [int(z.peak_cuttings)]));
		rows += line(__("Mother plants needed"),
			int(Math.round(z.peak_cuttings / z.cuttings_per_mother_per_week)),
			__("at {0} a mother a week", [z.cuttings_per_mother_per_week]));
		rows += line(__("Generations standing"), c.pick.generations,
			__("the plantlets, plus one a multiplication"));
		rows += line(__("Plantlets to order"), int(c.tc),
			__("rounded up to a planting area of {0}", [int(z.min_planting_area)]));
		rows += line(__("Order by"), day(c.order_by));
		rows += line(__("First cutting"), day(c.first_cut_date));
		rows += line(__("Pool it becomes"), int(c.peak_pool), __("mother plants"));
		rows += line(__("Block cleared"), day(c.line_end_date),
			__("{0} cutting weeks left", [c.pick.cutting_weeks]));
		rows += line(__("Planting weeks met"), `${c.weeks_met} / ${c.weeks}`,
			c.shortfall ? __("{0} cuttings short", [int(c.shortfall)]) : "");
		rows += line(__("Standing motherstock"), int((o.standing || {}).plants),
			__("shown, not taken off the order"));

		const opts = (z.options || []).map((x) =>
			`<tr${x.cycles === c.cycles ? ' style="font-weight:600;background:var(--control-bg,#f4f5f6)"' : ""}` +
			` data-mult="${x.cycles}" style="cursor:pointer">` +
			`<td>${x.cycles}${x.cycles === z.suggested ? " ★" : ""}</td>` +
			`<td class="text-right">${x.generations}</td>` +
			`<td class="text-right">${int(x.buy)}</td>` +
			`<td>${day(x.order_by)}</td>` +
			`<td class="text-right"` +
			`${x.covers_season ? "" : ' style="color:var(--red-600,#c0392b)"' +
				` title="${esc(__("the line is cleared before the season ends"))}"`}>` +
			`${x.cutting_weeks}${x.covers_season ? "" : " !"}</td></tr>`).join("");

		gens.html(
			`<table style="font-size:.85rem;margin:0 0 12px 0">${rows}</table>` +
			`<div class="text-muted small" style="text-transform:uppercase;` +
			`letter-spacing:.05em;margin-bottom:4px">${__("What each multiplication costs")}</div>` +
			`<table class="table table-bordered" style="font-size:.78rem;margin:0">` +
			`<thead><tr><th>${__("Multiply")}</th><th class="text-right">${__("Gens")}</th>` +
			`<th class="text-right">${__("Plantlets")}</th><th>${__("Order by")}</th>` +
			`<th class="text-right">${__("Cutting weeks")}</th>` +
			`</tr></thead><tbody>${opts}</tbody></table>` +
			`<p class="text-muted small">★ ${esc(__(
				"what the arithmetic suggests. Each multiplication is one "
				+ "establishment spent reaching the full pool, taken out of the "
				+ "line's life rather than added to it — the block is cleared one "
				+ "life after its FIRST cut."))}</p>` +
			gen_table(c) + week_table(c) +
			((o.assumed || []).length
				? `<p class="small" style="color:var(--orange-700,#b45309)">` +
				  (o.assumed || []).map(esc).join("<br>") + `</p>`
				: ""));

		gens.find("tr[data-mult]").on("click", function () {
			const n = parseInt(this.dataset.mult, 10);
			seed("tc_divert", n);
			load({ cycles: n });
		});
	};

	const load = (args) => {
		if (busy) return Promise.resolve();
		busy = true;
		return frappe.call({ method: M + "peak_options",
			args: Object.assign({ production_plan: frm.doc.name }, args || {}) })
			.then((r) => {
				const o = r.message;
				const c = o && o.schedule;
				if (c) {
					last = { tc: c.tc, divert: c.cycles };
					// Straight at the input as well as the model: set_value alone
					// leaves the box showing whatever it held before. Seeded before
					// the draw, because a throw inside show() used to abort the
					// whole .then and leave both boxes blank.
					seed("tc_qty", c.tc);
					seed("tc_divert", c.cycles);
				}
				show(o);
			})
			.always(() => { busy = false; });
	};

	// Which box was touched decides what is asked of the server. Moving the weeks
	// re-solves the quantity, because the cheapest order that covers is a
	// different number at a different diversion and keeping the old one reports a
	// shortfall against a figure nobody chose. Typing a quantity keeps it, because
	// that is the whole point of typing it.
	//
	// Frappe fires df.onchange for a user edit and not for a programmatic set, so
	// this listens to the browser's own change event; a reload writing the box back
	// over the reader's typing looked like the field refusing to take a value.
	const val = (f) => {
		const $i = d.fields_dict[f].$input;
		const n = parseInt($i ? $i.val() : "", 10);
		return isNaN(n) ? null : n;
	};
	const pick = (edited) => {
		if (busy) return;
		const tc = val("tc_qty");
		const dv = val("tc_divert");
		if (tc === last.tc && dv === last.divert) return;
		// Changing the multiplication re-sizes the order; typing a quantity keeps it.
		load(edited === "tc_divert"
			? { cycles: dv }
			: { tc: tc || null, cycles: dv });
	};

	load().then(() => {
		["tc_qty", "tc_divert"].forEach((f) => {
			const $i = d.fields_dict[f].$input;
			if ($i) { $i.on("change", () => pick(f)); }
		});
	});
}


// Open the motherstock line for this plan, making it if it is not there yet. The
// figures it settles on -- plantlets to order, how many times to multiply -- are
// what Plan Procurement then buys.
const plan_motherstock = (frm) => {
	frappe.db.get_value("Summer Flower Motherstock Plan",
		{ production_plan: frm.doc.name }, "name")
		.then((r) => {
			const found = r && r.message && r.message.name;
			if (found) {
				frappe.set_route("Form", "Summer Flower Motherstock Plan", found);
				return;
			}
			frappe.call({
				method: "upande_summer_flowers.summer_flowers.doctype"
					+ ".summer_flower_motherstock_plan.summer_flower_motherstock_plan"
					+ ".for_plan",
				args: { production_plan: frm.doc.name },
				freeze: true,
				freeze_message: __("Working out the motherstock line..."),
			}).then((x) => {
				if (x && x.message) {
					frappe.set_route("Form", "Summer Flower Motherstock Plan", x.message);
				}
			});
		});
};
