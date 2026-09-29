(function () {
  try {
    if (!document.querySelector('link[data-sfn-poppins]')) {
      var pf = document.createElement('link');
      pf.rel = 'stylesheet';
      pf.setAttribute('data-sfn-poppins', '1');
      pf.href = 'https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap';
      document.head.appendChild(pf);
    }
  } catch (e) {}

  // Role-gate tiles: data-roles="" shows for everyone; otherwise only if the user
  // holds one of the listed roles. System Manager / Administrator see all.
  var roles = (window.frappe && frappe.user_roles) || [];
  var isAdmin = roles.indexOf('System Manager') >= 0
    || roles.indexOf('Administrator') >= 0
    || (window.frappe && frappe.user && frappe.user.name === 'Administrator');
  root_element.querySelectorAll('.sfn-tile').forEach(function (tile) {
    var req = (tile.getAttribute('data-roles') || '')
      .split(',').map(function (s) { return s.trim(); }).filter(Boolean);
    if (req.length === 0) return;
    var ok = isAdmin || req.some(function (r) { return roles.indexOf(r) >= 0; });
    if (!ok) tile.classList.add('sfn-hide');
  });

  // Hide a section title if every tile under it is hidden.
  root_element.querySelectorAll('.sfn-title').forEach(function (t) {
    var grid = t.nextElementSibling;
    if (!grid || !grid.classList.contains('sfn-grid')) return;
    if (!grid.querySelectorAll('.sfn-tile:not(.sfn-hide)').length) {
      t.classList.add('sfn-hide');
      grid.classList.add('sfn-hide');
    }
  });

  // Live counts on the tiles that carry a data-count doctype. A tile without one
  // stays a plain link; a count that fails to load leaves no badge rather than a
  // zero, so nobody reads "0 to approve" off a broken request.
  var wanted = Array.prototype.slice.call(
    root_element.querySelectorAll('.sfn-tile[data-count]'));
  wanted.forEach(function (tile) {
    var doctype = tile.getAttribute('data-count');
    var filters = {};
    try { filters = JSON.parse(tile.getAttribute('data-filters') || '{}'); } catch (e) { return; }
    frappe.call({
      method: 'frappe.client.get_count',
      args: { doctype: doctype, filters: filters },
      callback: function (r) {
        var n = r && r.message;
        if (!n) return;
        var b = document.createElement('span');
        b.className = 'sfn-cnt' + (tile.hasAttribute('data-warn') ? ' sfn-warn' : '');
        // toLocaleString, not frappe.format: that one returns a right-aligned
        // <div> for Int and would put markup inside the badge. (frappe.utils has
        // no format_number in v16; the global one is not reachable from a shadow
        // block's script.)
        b.textContent = Number(n).toLocaleString();
        tile.appendChild(b);
      },
    });
  });
})();
