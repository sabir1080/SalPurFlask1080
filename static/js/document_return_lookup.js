/* Universal Document Return Lookup — one reusable server-side-search modal
 * for picking the original Sale-line / Purchase-line to return, the same
 * architecture as static/js/item_lookup.js and static/js/party_lookup.js
 * (debounced search, server-side pagination, never the whole table).
 *
 * Usage from a calling template:
 *   DocumentReturnLookup.open({
 *     type: "sale_return" | "purchase_return",
 *     partyId: <customer_id or supplier_id, optional>,
 *     onSelect: function(row) { ... }
 *   });
 * `row` is exactly one result shape returned by /api/sale-returns/lookup or
 * /api/purchase-returns/lookup:
 *   { id, sale_id|purchase_id, invoice_no, customer|supplier, date, item,
 *     unit, price, remaining }
 * `id` is the SaleItem/PurchaseItem id — the same value the existing
 * sale_item_id[]/purchase_item_id[] form field already expects, so callers
 * only need to swap how that value gets picked, not what it means.
 *
 * Never interpolates document/party/item text into innerHTML/template-literal
 * strings — every piece of server text is set via textContent, the same rule
 * item_lookup.js and party_lookup.js follow and for the same reason.
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
    sale_return: {
      title: "Select Sale / Item to Return",
      endpoint: "/api/sale-returns/lookup",
      placeholder: "Search by invoice #, customer, or item...",
      partyParam: "customer_id",
      partyCol: "customer",
      partyHeader: "Customer",
      docPrefix: "#SAL-",
      noResults: "No returnable sale items found. Try a different invoice #, customer, or item.",
    },
    purchase_return: {
      title: "Select Purchase / Item to Return",
      endpoint: "/api/purchase-returns/lookup",
      placeholder: "Search by purchase #, supplier, or item...",
      partyParam: "supplier_id",
      partyCol: "supplier",
      partyHeader: "Supplier",
      docPrefix: "#PUR-",
      noResults: "No returnable purchase items found. Try a different purchase #, supplier, or item.",
    },
  };

  function buildModalOnce() {
    if (els) return;

    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="modal fade" id="docRetLookupModal" tabindex="-1" aria-labelledby="docRetLookupTitle" aria-hidden="true">' +
        '<div class="modal-dialog modal-xl modal-dialog-scrollable">' +
          '<div class="modal-content">' +
            '<div class="modal-header">' +
              '<h5 class="modal-title" id="docRetLookupTitle"><i class="bi bi-search"></i> Select</h5>' +
              '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
            '</div>' +
            '<div class="modal-body">' +
              '<div class="row g-2 mb-2">' +
                '<div class="col-md-8">' +
                  '<input type="text" class="form-control" id="drlSearch" placeholder="Search..." autocomplete="off">' +
                '</div>' +
                '<div class="col-md-2">' +
                  '<input type="date" class="form-control" id="drlDateFrom" title="From date">' +
                '</div>' +
                '<div class="col-md-2">' +
                  '<input type="date" class="form-control" id="drlDateTo" title="To date">' +
                '</div>' +
              '</div>' +
              '<div id="drlStatus" class="small text-muted mb-2" aria-live="polite"></div>' +
              '<div class="table-responsive">' +
                '<table class="table table-sm table-hover align-middle mb-0" id="drlTable">' +
                  '<thead>' +
                    '<tr>' +
                      '<th>Document #</th>' +
                      '<th id="drlPartyHeader">Party</th>' +
                      '<th>Item</th>' +
                      '<th class="d-none d-sm-table-cell">Date</th>' +
                      '<th class="text-end">Price</th>' +
                      '<th class="text-end">Remaining</th>' +
                    '</tr>' +
                  '</thead>' +
                  '<tbody id="drlResults"></tbody>' +
                '</table>' +
              '</div>' +
            '</div>' +
            '<div class="modal-footer d-flex justify-content-between align-items-center">' +
              '<div class="small text-muted" id="drlPageInfo"></div>' +
              '<div class="btn-group">' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="drlPrev">' +
                  '<i class="bi bi-chevron-left"></i> Previous</button>' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="drlNext">' +
                  'Next <i class="bi bi-chevron-right"></i></button>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(wrap.firstElementChild);

    els = {
      modalEl: document.getElementById("docRetLookupModal"),
      title: document.getElementById("docRetLookupTitle"),
      search: document.getElementById("drlSearch"),
      dateFrom: document.getElementById("drlDateFrom"),
      dateTo: document.getElementById("drlDateTo"),
      partyHeader: document.getElementById("drlPartyHeader"),
      status: document.getElementById("drlStatus"),
      results: document.getElementById("drlResults"),
      pageInfo: document.getElementById("drlPageInfo"),
      prev: document.getElementById("drlPrev"),
      next: document.getElementById("drlNext"),
    };
    bsModal = new bootstrap.Modal(els.modalEl);

    els.search.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () { state.page = 1; runSearch(); }, DEBOUNCE_MS);
    });
    els.dateFrom.addEventListener("change", function () { state.page = 1; runSearch(); });
    els.dateTo.addEventListener("change", function () { state.page = 1; runSearch(); });
    els.prev.addEventListener("click", function () {
      if (state.page > 1) { state.page -= 1; runSearch(); }
    });
    els.next.addEventListener("click", function () {
      if (state.page < state.totalPages) { state.page += 1; runSearch(); }
    });
    els.results.addEventListener("click", function (e) {
      var row = e.target.closest("tr[data-row-idx]");
      if (row) selectRow(parseInt(row.dataset.rowIdx, 10));
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

    var labels = LABELS[state.type];
    var params = new URLSearchParams();
    params.set("q", els.search.value.trim());
    params.set("page", String(state.page));
    params.set("per_page", String(PAGE_SIZE));
    if (state.partyId) params.set(labels.partyParam, String(state.partyId));
    if (els.dateFrom.value) params.set("date_from", els.dateFrom.value);
    if (els.dateTo.value) params.set("date_to", els.dateTo.value);

    fetch(labels.endpoint + "?" + params.toString())
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
      .catch(function () {
        if (mySeq !== requestSeq) return;
        renderError();
      });
  }

  function renderResults() {
    var labels = LABELS[state.type];
    els.results.innerHTML = "";
    if (!state.rows.length) {
      setStatus("");
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 6;
      td.className = "text-center text-muted py-4";
      td.textContent = labels.noResults;
      tr.appendChild(td);
      els.results.appendChild(tr);
      els.pageInfo.textContent = "";
      els.prev.disabled = true;
      els.next.disabled = true;
      return;
    }
    setStatus("");
    state.rows.forEach(function (row, idx) {
      var tr = document.createElement("tr");
      tr.dataset.rowIdx = String(idx);
      tr.style.cursor = "pointer";
      tr.appendChild(cell(labels.docPrefix + row.invoice_no));
      tr.appendChild(cell(row[labels.partyCol] || "—"));
      tr.appendChild(cell(row.item || "—"));
      tr.appendChild(cell(row.date || "—", "d-none d-sm-table-cell"));
      tr.appendChild(cell(Number(row.price || 0).toFixed(2), "text-end"));
      tr.appendChild(cell(row.remaining + " " + (row.unit || ""), "text-end"));
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
    td.colSpan = 6;
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
    var rows = els.results.querySelectorAll("tr[data-row-idx]");
    rows.forEach(function (r) { r.classList.remove("table-active"); });
    if (state.highlighted >= 0 && rows[state.highlighted]) {
      rows[state.highlighted].classList.add("table-active");
      rows[state.highlighted].scrollIntoView({ block: "nearest" });
    }
  }

  function selectRow(idx) {
    var row = state.rows[idx];
    if (!row) return;
    var cb = state.onSelect;
    bsModal.hide();
    if (typeof cb === "function") cb(row);
  }

  /** Open the Document Return Lookup.
   * options.type — "sale_return" or "purchase_return" (required).
   * options.partyId — optional customer_id/supplier_id to scope results to.
   * options.onSelect(row) — called once, with the chosen row. */
  function open(options) {
    options = options || {};
    var type = options.type === "purchase_return" ? "purchase_return" : "sale_return";
    var labels = LABELS[type];
    buildModalOnce();
    state = {
      type: type, partyId: options.partyId || null, onSelect: options.onSelect,
      page: 1, total: 0, totalPages: 1,
      rows: [], highlighted: -1,
    };
    els.title.innerHTML = '<i class="bi bi-search"></i> ' + labels.title;
    els.partyHeader.textContent = labels.partyHeader;
    els.search.value = "";
    els.search.placeholder = labels.placeholder;
    els.dateFrom.value = "";
    els.dateTo.value = "";
    els.results.innerHTML = "";
    els.pageInfo.textContent = "";
    bsModal.show();
    runSearch();
  }

  window.DocumentReturnLookup = { open: open };
})();
