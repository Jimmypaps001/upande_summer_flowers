// Copyright (c) 2026, James Kiruga and contributors
// For license information, please see license.txt

// An approved procurement plan stops being a decision and becomes a set of
// dates. What it buys, whether that meets the demand, when the material lands
// and when the first stems come off are the questions anybody asks of one, and
// answering them meant opening the production plan, the propagation plan and
// the planting calendar in three tabs.

// Choosing who raises the material. A dialog rather than a silent run of the
// trough allocator: that allocator answers one question -- what will our own
// benches hold -- and when the answer is "nothing", two rows at 0% appeared
// with no way to say who should raise it instead. What is already chosen opens
// with it, so this changes an allocation rather than quietly adding a second.
function allocate_units(frm) {
	frm.call({ doc: frm.doc, method: "raising_choices", freeze: true,
		freeze_message: __("Reading the units and what is already chosen...") })
		.then((r) => raising_dialog(frm, (r && r.message) || {}));
}

function raising_dialog(frm, c) {
	const units = c.units || [], props = c.propagators || [];
	const ways = (c.ways && c.ways.length) ? c.ways : [""];
	let rows = (c.chosen || []).map((x) => ({
		raises: x.raises || ways[0], unit: x.unit, supplier: x.supplier,
		share_pct: flt(x.share_pct), rate: flt(x.rate),
	}));
	if (!rows.length) {
		rows = [{ raises: ways[0], unit: (units[0] || {}).name, share_pct: 100 }];
	}
	const need = (w) => cint((c.plants_by_way || {})[w] || 0);

	const d = new frappe.ui.Dialog({
		title: __("Who raises it"),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Save"),
		primary_action() {
			d.get_primary_btn().prop("disabled", true);
			frm.call({ doc: frm.doc, method: "set_raising",
				args: { rows: JSON.stringify(rows) }, freeze: true,
				freeze_message: __("Saving...") })
				.then(() => { d.hide(); frm.reload_doc(); });
		},
	});

	function draw() {
		const byWay = {};
		rows.forEach((x) => { byWay[x.raises] = (byWay[x.raises] || 0) + flt(x.share_pct); });
		const unitOpts = (sel) => units.map((u) =>
			`<option value="${frappe.utils.escape_html(u.name)}"${u.name === sel ? " selected" : ""}>` +
			`${frappe.utils.escape_html(u.name)} — ${format_number(u.free, null, 0)} free ` +
			`of ${format_number(u.capacity || 0, null, 0)}${u.here ? " · this farm" : ""}</option>`).join("");
		const propOpts = (sel) => props.length
			? props.map((p) =>
				`<option value="${frappe.utils.escape_html(p.name)}"${p.name === sel ? " selected" : ""}>` +
				`${frappe.utils.escape_html(p.name)} — ${cint(p.lead_weeks)} wk` +
				`${p.rate ? `, ${p.rate}/plant` : ""}</option>`).join("")
			: `<option value="">${__("No propagator is marked as raising plants for us")}</option>`;

		d.fields_dict.body.$wrapper.html(`
<style>
.rz-t{width:100%;border-collapse:collapse;font-size:.85rem}
.rz-t th{text-align:left;font-size:.68rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);padding:5px 6px;border-bottom:1px solid var(--border-color)}
.rz-t td{padding:5px 6px;border-bottom:1px solid var(--border-color)}
.rz-t select,.rz-t input{width:100%}
.rz-t td.n,.rz-t th.n{text-align:right}
.rz-gap{font-size:.8rem;margin-top:8px;color:var(--red-600,#c0392b)}
</style>
<table class="rz-t"><thead><tr>
  <th style="width:9rem">${__("Where")}</th>
  ${ways.length > 1 ? `<th style="width:9rem">${__("Raises")}</th>` : ""}
  <th>${__("Unit / propagator")}</th>
  <th class="n" style="width:6rem">${__("Share")}</th>
  <th class="n" style="width:6rem">${__("Plants")}</th><th style="width:2rem"></th>
</tr></thead><tbody>
${rows.map((x, i) => `<tr data-i="${i}">
  <td><select class="rz-kind">
    <option value="in"${x.supplier ? "" : " selected"}>${__("Our own unit")}</option>
    <option value="out"${x.supplier ? " selected" : ""}>${__("Outside propagator")}</option>
  </select></td>
  ${ways.length > 1 ? `<td><select class="rz-way">${ways.map((w) =>
    `<option${w === x.raises ? " selected" : ""}>${frappe.utils.escape_html(w)}</option>`).join("")}</select></td>` : ""}
  <td><select class="rz-party">${x.supplier ? propOpts(x.supplier) : unitOpts(x.unit)}</select></td>
  <td class="n"><input class="rz-share" type="number" min="0" max="100" step="0.1" value="${flt(x.share_pct)}"></td>
  <td class="n">${format_number(Math.round(need(x.raises) * flt(x.share_pct) / 100), null, 0)}</td>
  <td><a class="rz-del" style="cursor:pointer" title="${__("Remove")}">✕</a></td>
</tr>`).join("")}
</tbody></table>
<div style="margin-top:10px">
  <button class="btn btn-xs btn-default rz-add-in">${__("+ Our own unit")}</button>
  <button class="btn btn-xs btn-default rz-add-out">${__("+ Outside propagator")}</button>
  <button class="btn btn-xs btn-default rz-troughs">${__("Fill from our troughs")}</button>
</div>
${ways.map((w) => {
  const got = byWay[w] || 0;
  return Math.abs(got - 100) < 0.5 ? "" :
    `<div class="rz-gap">${frappe.utils.escape_html(w || __("The material"))}: ` +
    `${got.toFixed(1)}% ${__("allocated")}` +
    (got < 100 ? ` — ${format_number(Math.round(need(w) * (100 - got) / 100), null, 0)} ` +
      __("plants with nobody raising them") : "") + `</div>`;
}).join("")}`);

		const $w = d.fields_dict.body.$wrapper;
		$w.find("tr[data-i]").each(function () {
			const i = cint($(this).data("i")), row = rows[i];
			$(this).find(".rz-kind").on("change", function () {
				if ($(this).val() === "out") {
					row.supplier = (props[0] || {}).name; row.unit = null;
					row.rate = (props[0] || {}).rate || 0;
				} else { row.unit = (units[0] || {}).name; row.supplier = null; row.rate = 0; }
				draw();
			});
			$(this).find(".rz-way").on("change", function () { row.raises = $(this).val(); draw(); });
			$(this).find(".rz-party").on("change", function () {
				if (row.supplier) {
					row.supplier = $(this).val();
					row.rate = (props.find((p) => p.name === row.supplier) || {}).rate || 0;
				} else row.unit = $(this).val();
			});
			$(this).find(".rz-share").on("change", function () {
				row.share_pct = flt($(this).val()); draw();
			});
			$(this).find(".rz-del").on("click", () => { rows.splice(i, 1); draw(); });
		});
		$w.find(".rz-add-in").on("click", () => {
			rows.push({ raises: ways[0], unit: (units[0] || {}).name, share_pct: 0 }); draw();
		});
		$w.find(".rz-add-out").on("click", () => {
			const p0 = props[0] || {};
			rows.push({ raises: ways[0], supplier: p0.name, rate: p0.rate || 0, share_pct: 0 });
			draw();
		});
		// Still offered, for the farm that wants the troughs to answer. It just
		// is not what pressing the button does any more.
		$w.find(".rz-troughs").on("click", () => {
			frm.call({ doc: frm.doc, method: "propose_allocation", freeze: true,
				freeze_message: __("Working the season through each unit's troughs...") })
				.then(() => { d.hide(); frm.reload_doc(); });
		});
	}

	draw();
	d.show();
}

// A share is only meaningful once it is a share of something.// A share is only meaningful once it is a share of something. The pool is the
// production plan's weekly plantings summed -- the same figure propose_allocation
// divides -- fetched once per form so typing a percentage can answer in plants
// immediately rather than after a round trip per keystroke.
function load_pool(frm) {
	if (!frm.doc.production_plan) { frm._alloc_pool = null; return Promise.resolve(); }
	return frm.call({ doc: frm.doc, method: "allocation_pool" }).then((r) => {
		frm._alloc_pool = (r && r.message) || null;
		paint_allocation(frm);
	});
}

// Plants from a share, and the share back from plants, so the two columns agree
// whichever one was typed in. Rounded to whole plants because half a plant is
// not a thing anyone can stick.
function alloc_plants(frm, row) {
	const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
	if (!pool) return null;
	return Math.round((flt(row.share_pct) * pool) / 100);
}

function paint_allocation(frm) {
	const grid = frm.fields_dict.propagation_allocation;
	if (!grid || !grid.grid) return;
	const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
	const rows = frm.doc.propagation_allocation || [];
	const pct = rows.reduce((a, r) => a + flt(r.share_pct), 0);
	const plants = rows.reduce((a, r) => a + cint(r.plants), 0);
	if (!pool) { grid.$wrapper.find(".sfp-alloc-note").remove(); return; }
	const left = pool - plants;
	const msg = __("{0} of {1} plants allocated ({2}%). {3}", [
		format_number(plants, null, 0), format_number(pool, null, 0),
		pct.toFixed(2),
		left > 0 ? __("{0} still to buy in as plants.", [format_number(left, null, 0)])
			: (left < 0 ? __("Over-allocated by {0}.", [format_number(-left, null, 0)])
				: __("Fully allocated.")),
	]);
	grid.$wrapper.find(".sfp-alloc-note").remove();
	grid.$wrapper.prepend(
		`<p class="sfp-alloc-note text-muted" style="font-size:.8rem;margin:0 0 6px"${
			left < 0 ? ' data-over="1"' : ""}>${frappe.utils.escape_html(msg)}</p>`);
}

frappe.ui.form.on("Summer Flower Propagation Allocation", {
	// Typing a share fills the plants beside it. The unit's own columns --
	// planted, surplus, troughs -- stay blank until the troughs are actually
	// worked through, because only "Choose Propagation Units" knows those.
	share_pct(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const n = alloc_plants(frm, row);
		if (n === null) {
			frappe.show_alert({ message: __("No production plan, so a share has nothing to be a share of."), indicator: "orange" });
			return;
		}
		frappe.model.set_value(cdt, cdn, "plants", n).then(() => paint_allocation(frm));
	},
	// And the other way: an absolute number sets the share it represents, so a
	// row entered either way reads the same.
	plants(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		const pool = frm._alloc_pool && frm._alloc_pool.season_plants;
		if (!pool) return;
		const pct = Math.round((cint(row.plants) * 10000) / pool) / 100;
		if (flt(row.share_pct).toFixed(2) !== pct.toFixed(2)) {
			frappe.model.set_value(cdt, cdn, "share_pct", pct)
				.then(() => paint_allocation(frm));
		} else {
			paint_allocation(frm);
		}
	},
	// The running total has to follow every way the table can change, not only
	// the two fields above: a row removed, a unit swapped, or a row added by
	// "Choose Propagation Units" all move it.
	propagation_allocation_remove(frm) { paint_allocation(frm); },
	propagation_allocation_add(frm) { paint_allocation(frm); },
	propagation_unit(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.propagation_unit && !row.alloc_type) {
			frappe.model.set_value(cdt, cdn, "alloc_type", "Own Unit");
		}
		paint_allocation(frm);
	},
	// A propagator's record carries what they charge and how much notice they
	// need, so choosing one fills both rather than asking for them again.
	supplier(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.supplier) return;
		frappe.model.set_value(cdt, cdn, "alloc_type", "Outside Propagator");
		frappe.db.get_value("Supplier", row.supplier,
			["custom_sf_propagation_weeks", "custom_sf_rate_per_plant"]).then((r) => {
			const v = (r && r.message) || {};
			if (cint(v.custom_sf_propagation_weeks) && !cint(row.lead_weeks)) {
				frappe.model.set_value(cdt, cdn, "lead_weeks", cint(v.custom_sf_propagation_weeks));
			}
			if (flt(v.custom_sf_rate_per_plant) && !flt(row.rate_per_plant)) {
				frappe.model.set_value(cdt, cdn, "rate_per_plant", flt(v.custom_sf_rate_per_plant));
			}
			if (!cint(v.custom_sf_propagation_weeks)) {
				frappe.show_alert({ indicator: "orange", message: __(
					"{0} has no propagation lead time on record, so type the weeks on the row.",
					[row.supplier]) });
			}
		});
	},
	alloc_type(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.alloc_type === "Outside Propagator") {
			frappe.model.set_value(cdt, cdn, "propagation_unit", null);
		} else {
			frappe.model.set_value(cdt, cdn, "supplier", null);
			frappe.model.set_value(cdt, cdn, "rate_per_plant", 0);
		}
	},
});

frappe.ui.form.on("Summer Flower Purchase Allocation", {
	// A supplier's own notice beats the protocol's, the way the propagator's
	// does on the allocation below it.
	supplier(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.supplier) return;
		frappe.db.get_value("Supplier", row.supplier, "custom_sf_propagation_weeks")
			.then((r) => {
				const w = cint(((r || {}).message || {}).custom_sf_propagation_weeks);
				if (w && !cint(row.lead_weeks)) {
					frappe.model.set_value(cdt, cdn, "lead_weeks", w);
				}
			});
	},
});

frappe.ui.form.on("Summer Flower Procurement Plan", {
	refresh(frm) {
		load_pool(frm);
		load_ways(frm);
		if (frm.doc.docstatus === 0 && frm.doc.production_plan) {
			frm.add_custom_button(__("Choose Propagation Units"),
				() => allocate_units(frm));
			frm.add_custom_button(__("Change Order Quantity"),
				() => resize_order(frm));
			// Buying and raising are two decisions. A crop bought as tissue
			// culture and propagated here needs both: who sells it, and who
			// raises what they send.
			if (frm.doc.method === "Purchase") {
				frm.add_custom_button(__("Choose Suppliers"), () =>
					frm.call({
						doc: frm.doc, method: "propose_purchase", freeze: true,
						freeze_message: __("Listing what has to be bought..."),
					}).then((r) => {
						const m = (r || {}).message || {};
						frm.reload_doc();
						frappe.show_alert({ indicator: "blue", message: __(
							"A line per way in: {0}. Set the share each supplier sends.",
							[(m.stages || []).join(", ")]) });
					}));
			}
		}

		if (frm.doc.docstatus === 1) {
			// Runnable whenever, not only at the moment of approval. A cohort
			// that had no block when this was approved never got an entry, and
			// an approved plan cannot be approved again.
			frm.add_custom_button(__("Write Planting Plan"), () =>
				frm.call({
					doc: frm.doc, method: "write_planting_plan", freeze: true,
					freeze_message: __("Writing the planting plan..."),
				}).then((r) => {
					const n = ((r || {}).message || {}).written || 0;
					frappe.show_alert({
						indicator: n ? "green" : "blue",
						message: n
							? __("{0} plantings written to the calendar.", [n])
							: __("Nothing new to write."),
					});
					frm.reload_doc();
				}));
		}
		if (frm.doc.docstatus !== 1) {
			frm.get_field("overview_html").$wrapper.empty();
			return;
		}
		frm.call({ doc: frm.doc, method: "overview_for_form" })
			.then((r) => sf_proc_overview(frm, r.message));
	},

	production_plan(frm) {
		load_pool(frm);
	},
});

function sf_proc_overview(frm, o) {
	const el = frm.get_field("overview_html").$wrapper;
	if (!o) { el.empty(); return; }
	const esc = frappe.utils.escape_html;
	const int = (n) => format_number(n || 0, null, 0);
	const day = (d) => (d ? frappe.datetime.str_to_user(d) : "—");

	const tile = (k, v, m, c) =>
		`<div class="sfp-tile"><div class="k">${esc(k)}</div>` +
		`<div class="v"${c ? ` style="color:var(${c})"` : ""}>${v}</div>` +
		`<div class="m">${m || ""}</div></div>`;

	const b = o.buying, dm = o.demand, w = o.when, g = o.ground;
	const cov = dm.coverage_pct || 0;
	const after = dm.after_risk_pct;

	const tiles = [
		tile(__("Buying"), int(b.units),
			`${esc(b.entry_stage || b.method || "")}` +
			(b.overridden ? ` · ${__("set by hand, sum said {0}", [int(b.calculated)])}` : "") +
			(b.supplier ? ` · ${esc(b.supplier)}` : ""),
			b.overridden ? "--warning" : ""),
		tile(__("Order by"), day(b.order_by),
			b.order_late ? __("that date has passed") : __("to arrive in time"),
			b.order_late ? "--critical" : "--good"),
	];
	if (b.propagates_here) {
		tiles.push(tile(__("Pool it builds"), int(b.pool),
			__("mother plants · {0} cuttings a week · {1} cycles",
				[int(b.weekly_draw), b.cycles])));
		tiles.push(tile(__("Cutting weeks"),
			b.cutting_weeks == null ? "—" : int(b.cutting_weeks),
			__("left in the line at {0} cycles", [b.cycles]),
			!b.cutting_weeks ? "--critical"
				: (b.cutting_weeks < 13 ? "--serious" : "--good")));
	}
	// What the order ACTUALLY makes, whichever way the crop is got. A quantity
	// set by hand is a decision about money, and the plants it does not make are
	// what that decision costs -- said here rather than discovered when the
	// planting weeks run short.
	if (b.makes) {
		const gap = b.cuttings_short
			? __("{0} cuttings short · {1} weeks not covered",
				[int(b.cuttings_short), int(b.weeks_short)])
			: (b.plants_short
				? __("{0} plants short · {1} plantings not supplied",
					[int(b.plants_short), int(b.weeks_short)])
				: __("covers every planting"));
		tiles.push(tile(__("Plants it makes"), int(b.makes), gap,
			(b.cuttings_short || b.plants_short) ? "--critical" : "--good"));
	}
	tiles.push(tile(__("Demand met"), cov.toFixed(1) + "%",
		__("{0} of {1} stems", [int(dm.planned), int(dm.asked)]),
		cov >= 100 ? "--good" : "--serious"));
	if (dm.at_risk) {
		tiles.push(tile(__("After cuttings short"),
			after == null ? "—" : after.toFixed(1) + "%",
			dm.risk_exceeds_plan
				? __("every stem this plan grows is short of cuttings")
				: __("{0} stems have no cuttings to grow them", [int(dm.at_risk)]),
			"--critical"));
	}
	tiles.push(tile(__("Going in the ground"), int(g.plants),
		__("{0} plantings · {1} beds of {2}", [int(g.plantings), int(g.beds),
			int(g.beds_available)]),
		g.capped ? "--serious" : ""));
	tiles.push(tile(__("First harvest"), w.first_harvest_week || "—",
		w.last_harvest_week && w.last_harvest_week !== w.first_harvest_week
			? __("through {0}", [w.last_harvest_week]) : ""));

	// Order, arrive, plant, cut -- the four dates a cohort has, on one row.
	const rows = (o.arrivals || []).map((a) =>
		`<tr${a.late ? ' style="color:var(--red-600,#c0392b)"' : ""}>` +
		`<td>${esc(a.planting_week || "—")}</td>` +
		`<td>${day(a.order_by)}</td>` +
		`<td>${day(a.arrives)}</td>` +
		`<td>${day(a.planting_date)}</td>` +
		`<td>${esc(a.first_harvest_week || "—")}</td>` +
		`<td class="text-right">${int(a.plants)}</td>` +
		`<td class="text-right">${int(a.beds)}</td>` +
		`<td class="text-right">${a.from_pool ? __("off the pool") : int(a.units)}</td>` +
		`</tr>`).join("");

	// Who raises the plants, what that costs and when it hands them over. The
	// same table the plan was saved with, read back -- so the dashboard cannot
	// say anything the allocation does not.
	const rb = o.raised_by || [];
	const raised = !rb.length ? "" : `
  <p class="sfp-basis" style="margin:14px 0 5px;font-weight:600;color:var(--text-color)">${__("Who raises the plants")}</p>
  <table class="sfp-t" style="margin-bottom:14px"><thead><tr>
    <th>${__("Type")}</th><th>${__("Unit / Propagator")}</th>
    <th class="text-right">${__("Share")}</th><th class="text-right">${__("Plants")}</th>
    <th class="text-right">${__("Per plant")}</th><th class="text-right">${__("Amount")}</th>
    <th>${__("Start / order by")}</th><th>${__("Plants from")}</th><th>${__("Notes")}</th>
  </tr></thead><tbody>${rb.map((r) => `<tr>
    <td>${esc(r.type)}</td>
    <td>${esc(r.party || "—")}${r.propagation_plan
			? ` · <a href="/app/summer-flower-propagation-plan/${encodeURIComponent(r.propagation_plan)}">${__("plan")}</a>` : ""}${
		r.purchase_order
			? ` · <a href="/app/purchase-order/${encodeURIComponent(r.purchase_order)}">${__("PO")}</a>` : ""}</td>
    <td class="text-right">${flt(r.share_pct).toFixed(1)}%</td>
    <td class="text-right">${int(r.plants)}</td>
    <td class="text-right">${r.rate ? format_currency(r.rate) : "—"}</td>
    <td class="text-right">${r.amount ? format_currency(r.amount) : "—"}</td>
    <td>${day(r.start_by)}</td>
    <td>${day(r.plants_from)}${r.lead_weeks ? ` <span class="text-muted">(${__("{0}w", [r.lead_weeks])})</span>` : ""}</td>
    <td>${esc(r.note || r.basis || "")}</td>
  </tr>`).join("")}</tbody></table>`;

	el.html(`<style>
.sfp-wrap{margin:0 0 6px}
.sfp-tiles{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));margin-bottom:14px}
.sfp-tile{border:1px solid var(--border-color);border-radius:8px;padding:9px 11px;background:var(--card-bg)}
.sfp-tile .k{font-size:.66rem;text-transform:uppercase;letter-spacing:.5px;color:var(--text-muted)}
.sfp-tile .v{font-size:1.18rem;font-weight:600;line-height:1.35;font-variant-numeric:tabular-nums}
.sfp-tile .m{font-size:.72rem;color:var(--text-muted)}
.sfp-basis{font-size:.76rem;color:var(--text-muted);margin:0 0 12px}
table.sfp-t{width:100%;border-collapse:collapse;font-size:.78rem}
table.sfp-t th{text-align:left;font-size:.64rem;text-transform:uppercase;letter-spacing:.5px;
  color:var(--text-muted);padding:5px 8px;border-bottom:1px solid var(--border-color)}
table.sfp-t td{padding:4px 8px;border-bottom:1px solid var(--border-color);
  font-variant-numeric:tabular-nums}
table.sfp-t th.text-right,table.sfp-t td.text-right{text-align:right}
.sfp-scroll{max-height:340px;overflow:auto;border:1px solid var(--border-color);border-radius:8px}
</style>
<div class="sfp-wrap">
  <div class="sfp-tiles">${tiles.join("")}</div>
  ${b.basis ? `<p class="sfp-basis">${esc(b.basis)}</p>` : ""}
  ${dm.risk_note ? `<p class="sfp-basis">${esc(dm.risk_note)}</p>` : ""}
  ${raised}
  <div class="sfp-scroll"><table class="sfp-t"><thead><tr>
    <th>${__("Planting week")}</th><th>${__("Order by")}</th><th>${__("Material arrives")}</th>
    <th>${__("Planted")}</th><th>${__("First harvest")}</th>
    <th class="text-right">${__("Plants")}</th><th class="text-right">${__("Beds")}</th>
    <th class="text-right">${__("To buy")}</th>
  </tr></thead><tbody>${rows}</tbody></table></div>
</div>`);
}
