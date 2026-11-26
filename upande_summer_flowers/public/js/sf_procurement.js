/* The production plan's procurement plan: save it as a draft from here; it is
 * submitted when the production plan is approved.
 *   sfProcurement.load(el, plan, call?, {editable, onSaved})
 */
(function () {
	if (window.sfProcurement) return;
	const M = "upande_summer_flowers.summer_flowers.procurement_edit.";
	const esc = (v) => String(v == null ? "" : v).replace(/[&<>"]/g, (c) =>
		({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
	const num = (v) => Math.round(+v || 0).toLocaleString();

	function render(el, s, o) {
		const edit = o.editable && s.docstatus === 0;
		const live = (s.procurement || []).filter((p) => p.docstatus < 2);
		let h = `<div class="sfo"><h4>Procurement plan <small>${s.deliveries} deliveries · ${num(s.units)} units · `
			+ `${esc((s.suppliers || []).join(", ") || "no supplier yet")}</small></h4>`;
		if (!live.length) {
			h += `<div class="sfo-empty">No procurement plan yet.${edit ? " Save one from this plan's suppliers and deliveries." : ""}</div>`;
		} else {
			h += `<div class="sfo-scroll"><table><thead><tr><th>Procurement plan</th><th>Status</th><th>Bought as</th>`
				+ `<th>Suppliers</th><th>Propagators</th><th class="r">Lines</th></tr></thead><tbody>`
				+ live.map((p) => `<tr><td><a href="/app/summer-flower-procurement-plan/${encodeURIComponent(p.name)}" target="_blank">${esc(p.name)}</a></td>`
					+ `<td>${esc(p.status)}</td><td>${esc(p.entry_stage || p.method || "")}</td>`
					+ `<td>${(p.sellers || []).map((x) => `${esc(x.supplier)} ${x.share_pct}%`).join("<br>") || "—"}</td>`
					+ `<td>${(p.raisers || []).map((x) => `${esc(x.party)} ${x.share_pct}%`).join("<br>") || "—"}</td>`
					+ `<td class="r">${p.lines}</td></tr>`).join("")
				+ `</tbody></table></div>`;
		}
		if (edit) {
			h += `<div style="margin-top:8px"><button class="sfo-btn pri" data-save>${live.length ? "Update the procurement plan" : "Save as a procurement plan"}</button></div>`
				+ `<div class="sfo-note">Saved as a draft; it is submitted when this plan is approved.</div>`;
		}
		h += `</div>`;
		el.innerHTML = h;
		const b = el.querySelector("[data-save]");
		if (b) b.addEventListener("click", () => {
			b.disabled = true; b.textContent = "Saving…";
			o.call(M + "save_draft", { plan: s.plan }).then((n) => { render(el, n, o); if (o.onSaved) o.onSaved(n); },
				(e) => { render(el, s, o); const m = (e && e.message) || String(e);
					if (window.frappe && frappe.msgprint) frappe.msgprint(m); else alert(m); });
		});
	}

	function load(el, plan, call, opts) {
		if (!el || !plan) return Promise.resolve();
		if (window.sfPlanOverview && window.sfPlanOverview.style) window.sfPlanOverview.style();
		const o = Object.assign({}, opts || {});
		o.call = call || ((m, a) => frappe.call({ method: m, args: a }).then((r) => r && r.message));
		el.innerHTML = `<div class="sfo"><div class="sfo-empty">Loading…</div></div>`;
		return o.call(M + "procurement", { plan }).then((s) => render(el, s, o),
			(e) => { el.innerHTML = `<div class="sfo"><div class="sfo-empty">Could not load: ${esc((e && e.message) || e)}</div></div>`; });
	}

	window.sfProcurement = { load, render };
})();
