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
			// Plan it, or look at what was agreed. Which of the two depends on
			// whether a line exists, and that is worth saying on the button: a
			// plan that says "Plan Motherstock" after somebody has already agreed
			// one invites a second answer to a settled question.
			//
			// Plan Procurement used to sit here with a dialog of its own that
			// sized the order a second time. The order follows from the agreed
			// line now and is written when the line is agreed, so there is
			// nothing left here to decide -- only something to look at.
			decided_buttons(frm);
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


// Plan the line on the dashboard, not on the document. The peak week, the weekly
// split and the shortfall are visible side by side there, which is what deciding
// the line actually needs; the Motherstock Plan stores what gets agreed and can
// still be opened directly. The scope travels in the query string and the tab in
// the hash, so the link lands on the motherstock pane already narrowed to this
// plan instead of on the variety chooser.
const motherstock_url = (frm) => {
	const q = new URLSearchParams({
		plan: frm.doc.name,
		variety: frm.doc.variety || "",
		farm: frm.doc.farm || "",
	});
	return `/summer-flowers-planning?${q.toString()}#motherstock`;
};

const plan_motherstock = (frm) => window.open(motherstock_url(frm), "_blank");

// The buttons that depend on what has already been decided: the line, and the
// order that follows from it. Neither is made here any more, so both read "View"
// once they exist.
//
// Revising is only offered while there is still time to act on it. An order that
// had to be placed last month cannot be re-sized by changing a document, and
// offering the button anyway is how somebody comes to believe they have changed
// something they have not.
const decided_buttons = (frm) => {
	Promise.all([
		frappe.db.get_value("Summer Flower Motherstock Plan",
			{ production_plan: frm.doc.name },
			["name", "tc_to_order", "multiplications", "order_by_date"]),
		// The order, if one has been written. Only ever shown, never made from
		// here: it is built from the agreed line at the moment that is agreed.
		frappe.db.get_value("Summer Flower Procurement Plan",
			{ production_plan: frm.doc.name, docstatus: ["<", 2] },
			["name", "total_units_to_order", "first_order_by"]),
	])
		.then(([r, pr]) => {
			const proc = pr && pr.message && pr.message.name ? pr.message : null;
			if (proc) {
				frm.add_custom_button(__("View Procurement"), () =>
					frappe.set_route("Form", "Summer Flower Procurement Plan",
						proc.name), ACTIONS);
			}
			const ms = r && r.message && r.message.name ? r.message : null;
			if (!ms) {
				frm.add_custom_button(__("Plan Motherstock"),
					() => plan_motherstock(frm), ACTIONS);
				return;
			}
			frm.add_custom_button(__("View Motherstock Plan"), () =>
				frappe.set_route("Form", "Summer Flower Motherstock Plan", ms.name),
				ACTIONS);

			const days = ms.order_by_date
				? frappe.datetime.get_day_diff(ms.order_by_date,
					frappe.datetime.get_today())
				: null;
			if (days === null || days > 0) {
				frm.add_custom_button(__("Revise Motherstock"),
					() => plan_motherstock(frm), ACTIONS);
			}
			// Said once, where the decision is read, rather than left for somebody
			// to work out from a date on another document.
			frm.dashboard.add_comment(
				days !== null && days <= 0
					? __("Motherstock agreed: {0} plantlets at {1} multiplication(s). "
						+ "The order date {2} has passed, so it can no longer be revised.",
						[format_number(ms.tc_to_order, null, 0), ms.multiplications,
						 frappe.datetime.str_to_user(ms.order_by_date)])
					: __("Motherstock agreed: {0} plantlets at {1} multiplication(s), "
						+ "to be ordered by {2} — {3} days left to revise it.",
						[format_number(ms.tc_to_order, null, 0), ms.multiplications,
						 ms.order_by_date
							? frappe.datetime.str_to_user(ms.order_by_date) : "—",
						 days]),
				days !== null && days <= 0 ? "red" : "blue", true);
		});
};
