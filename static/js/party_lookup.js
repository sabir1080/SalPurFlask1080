/* Universal Party Lookup — one reusable server-side-search modal for picking
 * a Customer or a Supplier, the same architecture as static/js/item_lookup.js
 * (debounced search, server-side pagination, never the whole table) but
 * separate: a customer/supplier has no categories, filters, units, or
 * SKU/barcode — just name/contact/address — so parameterizing item_lookup.js
 * for both would mean threading an entity-type flag through every function
 * in it for no shared code, rather than writing one small generic modal.
 *
 * Usage from a calling template:
 *   PartyLookup.open({ type: "customer", onSelect: function(party) { ... } });
 *   PartyLookup.open({ type: "supplier", onSelect: function(party) { ... } });
 * `party` is exactly one row shape returned by /api/customers/lookup or
 * /api/suppliers/lookup: { id, name, contact, address }.
 *
 * Never interpolates party names into innerHTML/template-literal strings —
 * every piece of server text is set via textContent, the same rule
 * item_lookup.js follows and for the same reason.
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 300;
  var PAGE_SIZE = 20;

  var state = null;
  var els = null;
  var bsModal = null;
  var debounceTimer = null;
  var requestSeq = 0;

  var LABELS = {
    customer: { title: "Select Customer", endpoint: "/api/customers/lookup", placeholder: "Search by name or phone..." },
    supplier: { title: "Select Supplier", endpoint: "/api/suppliers/lookup", placeholder: "Search by name or phone..." },
  };

  function buildModalOnce() {
    if (els) return;

    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="modal fade" id="partyLookupModal" tabindex="-1" aria-labelledby="partyLookupTitle" aria-hidden="true">' +
        '<div class="modal-dialog modal-lg modal-dialog-scrollable">' +
          '<div class="modal-content">' +
            '<div class="modal-header">' +
              '<h5 class="modal-title" id="partyLookupTitle"><i class="bi bi-search"></i> Select</h5>' +
              '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
            '</div>' +
            '<div class="modal-body">' +
              '<input type="text" class="form-control mb-2" id="plkSearch" placeholder="Search..." autocomplete="off">' +
              '<div id="plkStatus" class="small text-muted mb-2" aria-live="polite"></div>' +
              '<div class="table-responsive">' +
                '<table class="table table-sm table-hover align-middle mb-0" id="plkTable">' +
                  '<thead>' +
                    '<tr>' +
                      '<th>Name</th>' +
                      '<th class="d-none d-sm-table-cell">Contact</th>' +
                      '<th class="d-none d-md-table-cell">Address</th>' +
                    '</tr>' +
                  '</thead>' +
                  '<tbody id="plkResults"></tbody>' +
                '</table>' +
              '</div>' +
            '</div>' +
            '<div class="modal-footer d-flex justify-content-between align-items-center">' +
              '<div class="small text-muted" id="plkPageInfo"></div>' +
              '<div class="btn-group">' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="plkPrev">' +
                  '<i class="bi bi-chevron-left"></i> Previous</button>' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="plkNext">' +
                  'Next <i class="bi bi-chevron-right"></i></button>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(wrap.firstElementChild);

    els = {
      modalEl: document.getElementById("partyLookupModal"),
      title: document.getElementById("partyLookupTitle"),
      search: document.getElementById("plkSearch"),
      status: document.getElementById("plkStatus"),
      results: document.getElementById("plkResults"),
      pageInfo: document.getElementById("plkPageInfo"),
      prev: document.getElementById("plkPrev"),
      next: document.getElementById("plkNext"),
    };
    bsModal = new bootstrap.Modal(els.modalEl);

    els.search.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        state.page = 1;
        runSearch();
      }, DEBOUNCE_MS);
    });
    els.prev.addEventListener("click", function () {
      if (state.page > 1) { state.page -= 1; runSearch(); }
    });
    els.next.addEventListener("click", function () {
      if (state.page < state.totalPages) { state.page += 1; runSearch(); }
    });
    els.results.addEventListener("click", function (e) {
      var row = e.target.closest("tr[data-party-idx]");
      if (row) selectRow(parseInt(row.dataset.partyIdx, 10));
    });
    els.modalEl.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        moveHighlight(e.key === "ArrowDown" ? 1 : -1);
      } else if (e.key === "Enter") {
        if (document.activeElement === els.search && state.highlighted < 0 && state.rows.length) {
          state.highlighted = 0;
          renderHighlight();
        } else if (state.highlighted >= 0) {
          e.preventDefault();
          selectRow(state.highlighted);
        }
      }
    });
    els.modalEl.addEventListener("shown.bs.modal", function () {
      els.search.focus();
    });
  }

  function setStatus(text) { els.status.textContent = text || ""; }

  function runSearch() {
    var mySeq = ++requestSeq;
    setStatus("Searching…");
    els.results.innerHTML = "";

    var params = new URLSearchParams();
    params.set("q", els.search.value.trim());
    params.set("page", String(state.page));
    params.set("per_page", String(PAGE_SIZE));

    fetch(LABELS[state.type].endpoint + "?" + params.toString())
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (mySeq !== requestSeq) return;
        state.rows = data.results || [];
        state.total = data.total || 0;
        state.totalPages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
        state.highlighted = state.rows.length ? 0 : -1;
        renderResults();
      })
      .catch(function (err) {
        if (mySeq !== requestSeq) return;
        renderError();
      });
  }

  function renderResults() {
    els.results.innerHTML = "";
    if (!state.rows.length) {
      setStatus("");
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 3;
      td.className = "text-center text-muted py-4";
      td.textContent = "No results found. Try a different name or phone number.";
      tr.appendChild(td);
      els.results.appendChild(tr);
      els.pageInfo.textContent = "";
      els.prev.disabled = true;
      els.next.disabled = true;
      return;
    }
    setStatus("");
    state.rows.forEach(function (party, idx) {
      var tr = document.createElement("tr");
      tr.dataset.partyIdx = String(idx);
      tr.style.cursor = "pointer";
      tr.appendChild(cell(party.name));
      tr.appendChild(cell(party.contact || "—", "d-none d-sm-table-cell"));
      tr.appendChild(cell(party.address || "—", "d-none d-md-table-cell"));
      els.results.appendChild(tr);
    });
    var from = (state.page - 1) * PAGE_SIZE + 1;
    var to = Math.min(state.page * PAGE_SIZE, state.total);
    els.pageInfo.textContent = "Showing " + from + "–" + to + " of " + state.total + " results";
    els.prev.disabled = state.page <= 1;
    els.next.disabled = state.page >= state.totalPages;
    renderHighlight();
  }

  function cell(text, cls) {
    var td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text;
    return td;
  }

  function renderError() {
    setStatus("");
    els.results.innerHTML = "";
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = 3;
    td.className = "text-center text-danger py-4";
    td.textContent = "Could not load results. ";
    var retry = document.createElement("button");
    retry.type = "button";
    retry.className = "btn btn-sm btn-outline-danger ms-2";
    retry.textContent = "Retry";
    retry.addEventListener("click", runSearch);
    td.appendChild(retry);
    tr.appendChild(td);
    els.results.appendChild(tr);
    els.pageInfo.textContent = "";
  }

  function moveHighlight(delta) {
    if (!state.rows.length) return;
    state.highlighted = Math.max(0, Math.min(state.rows.length - 1, state.highlighted + delta));
    renderHighlight();
  }

  function renderHighlight() {
    var rows = els.results.querySelectorAll("tr[data-party-idx]");
    rows.forEach(function (r) { r.classList.remove("table-active"); });
    if (state.highlighted >= 0 && rows[state.highlighted]) {
      rows[state.highlighted].classList.add("table-active");
      rows[state.highlighted].scrollIntoView({ block: "nearest" });
    }
  }

  function selectRow(idx) {
    var party = state.rows[idx];
    if (!party) return;
    var cb = state.onSelect;
    bsModal.hide();
    if (typeof cb === "function") cb(party);
  }

  /** Open the Universal Party Lookup.
   * options.type — "customer" or "supplier" (required).
   * options.onSelect(party) — called once, with the chosen row. */
  function open(options) {
    options = options || {};
    var type = options.type === "supplier" ? "supplier" : "customer";
    buildModalOnce();
    state = {
      type: type, onSelect: options.onSelect,
      page: 1, total: 0, totalPages: 1,
      rows: [], highlighted: -1,
    };
    els.title.innerHTML = '<i class="bi bi-search"></i> ' + LABELS[type].title;
    els.search.value = "";
    els.search.placeholder = LABELS[type].placeholder;
    els.results.innerHTML = "";
    els.pageInfo.textContent = "";
    bsModal.show();
    runSearch();
  }

  window.PartyLookup = { open: open };
})();
