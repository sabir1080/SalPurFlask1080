/* Universal Item Lookup — one reusable server-side-search modal for every
 * transaction form that needs to pick an Item (Sale, Purchase, Quotation, and
 * their edit forms). Replaces embedding the whole item catalogue into the
 * page: the browser only ever holds the current page of search results
 * (<= 50 rows), never the full table.
 *
 * Usage from a calling template:
 *   ItemLookup.open({ onSelect: function(item) { ... } });
 * `item` is exactly one row shape returned by /api/items/lookup:
 *   { id, name, sku, barcode, category, unit, stock, sale_price }
 *
 * Never interpolates item names into innerHTML/template-literal strings —
 * every piece of server text is set via textContent, the same rule the
 * server-rendered templates already followed for the same reason (a name
 * containing `<`, a backtick, or `${...}` must never be able to run as
 * markup or script).
 */
(function () {
  "use strict";

  var DEBOUNCE_MS = 300;
  var PAGE_SIZE = 20;

  var state = null;   // set fresh each time open() runs
  var els = null;      // cached DOM refs into the (lazily built) modal
  var bsModal = null;
  var debounceTimer = null;
  var requestSeq = 0;  // guards against a slow, stale response overwriting a newer one

  function csrfToken() {
    var meta = document.querySelector('meta[name="csrf-token"]');
    return meta ? meta.getAttribute("content") : "";
  }

  function buildModalOnce() {
    if (els) return;

    var wrap = document.createElement("div");
    wrap.innerHTML =
      '<div class="modal fade" id="itemLookupModal" tabindex="-1" aria-labelledby="itemLookupTitle" aria-hidden="true">' +
        '<div class="modal-dialog modal-lg modal-dialog-scrollable">' +
          '<div class="modal-content">' +
            '<div class="modal-header">' +
              '<h5 class="modal-title" id="itemLookupTitle"><i class="bi bi-search"></i> Select Item</h5>' +
              '<button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>' +
            '</div>' +
            '<div class="modal-body">' +
              '<div class="row g-2 mb-2">' +
                '<div class="col-md-7">' +
                  '<input type="text" class="form-control" id="ilkSearch" ' +
                    'placeholder="Search by name, SKU, or barcode..." autocomplete="off">' +
                '</div>' +
                '<div class="col-md-5">' +
                  '<select class="form-select" id="ilkCategory">' +
                    '<option value="">All Categories</option>' +
                  '</select>' +
                '</div>' +
              '</div>' +
              '<div class="row g-2 mb-2" id="ilkFilters"></div>' +
              '<div id="ilkStatus" class="small text-muted mb-2" aria-live="polite"></div>' +
              '<div class="table-responsive">' +
                '<table class="table table-sm table-hover align-middle mb-0" id="ilkTable">' +
                  '<thead>' +
                    '<tr>' +
                      '<th>Item</th>' +
                      '<th class="d-none d-md-table-cell">SKU</th>' +
                      '<th class="d-none d-md-table-cell">Barcode</th>' +
                      '<th class="d-none d-sm-table-cell">Category</th>' +
                      '<th class="text-end">Stock</th>' +
                      '<th class="text-end">Price</th>' +
                    '</tr>' +
                  '</thead>' +
                  '<tbody id="ilkResults"></tbody>' +
                '</table>' +
              '</div>' +
            '</div>' +
            '<div class="modal-footer d-flex justify-content-between align-items-center">' +
              '<div class="small text-muted" id="ilkPageInfo"></div>' +
              '<div class="btn-group">' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="ilkPrev">' +
                  '<i class="bi bi-chevron-left"></i> Previous</button>' +
                '<button type="button" class="btn btn-outline-secondary btn-sm" id="ilkNext">' +
                  'Next <i class="bi bi-chevron-right"></i></button>' +
              '</div>' +
            '</div>' +
          '</div>' +
        '</div>' +
      '</div>';
    document.body.appendChild(wrap.firstElementChild);

    els = {
      modalEl: document.getElementById("itemLookupModal"),
      search: document.getElementById("ilkSearch"),
      category: document.getElementById("ilkCategory"),
      filters: document.getElementById("ilkFilters"),
      status: document.getElementById("ilkStatus"),
      results: document.getElementById("ilkResults"),
      pageInfo: document.getElementById("ilkPageInfo"),
      prev: document.getElementById("ilkPrev"),
      next: document.getElementById("ilkNext"),
    };
    bsModal = new bootstrap.Modal(els.modalEl);

    els.search.addEventListener("input", function () {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(function () {
        state.page = 1;
        runSearch();
      }, DEBOUNCE_MS);
    });
    els.category.addEventListener("change", function () {
      state.page = 1;
      state.categoryId = els.category.value || null;
      state.filterValues = {};
      loadFilterFields(state.categoryId);
      runSearch();
    });
    els.prev.addEventListener("click", function () {
      if (state.page > 1) { state.page -= 1; runSearch(); }
    });
    els.next.addEventListener("click", function () {
      if (state.page < state.totalPages) { state.page += 1; runSearch(); }
    });
    els.results.addEventListener("click", function (e) {
      var row = e.target.closest("tr[data-item-idx]");
      if (row) selectRow(parseInt(row.dataset.itemIdx, 10));
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

  function loadCategories() {
    // The app's business categories are few (tens, not thousands) — a single
    // small GET here is not the "load everything" problem this feature
    // exists to avoid; it's the same shape as any other dropdown filter.
    // Reuses the existing admin-config API rather than adding a duplicate.
    return fetch("/admin/config/api/enabled-categories")
      .then(function (r) { return r.ok ? r.json() : { categories: [] }; })
      .then(function (data) {
        els.category.innerHTML = "";
        var blank = document.createElement("option");
        blank.value = "";
        blank.textContent = "All Categories";
        els.category.appendChild(blank);
        (data.categories || []).forEach(function (c) {
          var opt = document.createElement("option");
          opt.value = c.id;
          opt.textContent = c.name;
          els.category.appendChild(opt);
        });
      })
      .catch(function () { /* category dropdown is a convenience, not required for search */ });
  }

  function loadFilterFields(categoryId) {
    els.filters.innerHTML = "";
    if (!categoryId) return;
    fetch("/api/items/filter-fields/" + encodeURIComponent(categoryId))
      .then(function (r) { return r.json(); })
      .then(function (data) {
        (data.fields || []).forEach(function (f) {
          var col = document.createElement("div");
          col.className = "col-md-3 col-sm-4";
          var label = document.createElement("label");
          label.className = "form-label small mb-1";
          label.textContent = f.field_label;
          col.appendChild(label);

          var input;
          if (f.field_type === "select" && Array.isArray(f.options)) {
            input = document.createElement("select");
            input.className = "form-select form-select-sm";
            var blank = document.createElement("option");
            blank.value = "";
            blank.textContent = "Any";
            input.appendChild(blank);
            f.options.forEach(function (opt) {
              var o = document.createElement("option");
              o.value = opt;
              o.textContent = opt;
              input.appendChild(o);
            });
          } else {
            input = document.createElement("input");
            input.type = f.field_type === "number" ? "number" : (f.field_type === "date" ? "date" : "text");
            input.className = "form-control form-control-sm";
            input.placeholder = f.field_label;
          }
          input.dataset.fieldName = f.field_name;
          input.addEventListener("change", function () {
            state.filterValues[f.field_name] = input.value;
            state.page = 1;
            runSearch();
          });
          if (input.tagName === "INPUT") {
            input.addEventListener("input", function () {
              clearTimeout(debounceTimer);
              debounceTimer = setTimeout(function () {
                state.filterValues[f.field_name] = input.value;
                state.page = 1;
                runSearch();
              }, DEBOUNCE_MS);
            });
          }
          col.appendChild(input);
          els.filters.appendChild(col);
        });
      })
      .catch(function () { /* filters are an enhancement; search still works without them */ });
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
    if (state.categoryId) params.set("category_id", state.categoryId);
    Object.keys(state.filterValues).forEach(function (name) {
      var v = state.filterValues[name];
      if (v) params.set("filter_" + name, v);
    });

    fetch("/api/items/lookup?" + params.toString())
      .then(function (r) {
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (data) {
        if (mySeq !== requestSeq) return;  // a newer search already superseded this one
        state.rows = data.results || [];
        state.total = data.total || 0;
        state.totalPages = Math.max(1, Math.ceil(state.total / PAGE_SIZE));
        state.highlighted = state.rows.length ? 0 : -1;
        renderResults();
      })
      .catch(function (err) {
        if (mySeq !== requestSeq) return;
        renderError(err);
      });
  }

  function renderResults() {
    els.results.innerHTML = "";
    if (!state.rows.length) {
      setStatus("");
      var tr = document.createElement("tr");
      var td = document.createElement("td");
      td.colSpan = 6;
      td.className = "text-center text-muted py-4";
      td.textContent = "No items found. Try a different name, SKU, barcode or filter.";
      tr.appendChild(td);
      els.results.appendChild(tr);
      els.pageInfo.textContent = "";
      els.prev.disabled = true;
      els.next.disabled = true;
      return;
    }
    setStatus("");
    state.rows.forEach(function (item, idx) {
      var tr = document.createElement("tr");
      tr.dataset.itemIdx = String(idx);
      tr.style.cursor = "pointer";
      tr.appendChild(cell(item.name));
      tr.appendChild(cell(item.sku || "—", "d-none d-md-table-cell"));
      tr.appendChild(cell(item.barcode || "—", "d-none d-md-table-cell"));
      tr.appendChild(cell(item.category || "—", "d-none d-sm-table-cell"));
      tr.appendChild(cell(String(item.stock != null ? item.stock : "—"), "text-end"));
      tr.appendChild(cell((item.sale_price || 0).toFixed(2), "text-end"));
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

  function renderError(err) {
    setStatus("");
    els.results.innerHTML = "";
    var tr = document.createElement("tr");
    var td = document.createElement("td");
    td.colSpan = 6;
    td.className = "text-center text-danger py-4";
    td.textContent = "Could not load items. ";
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
    var rows = els.results.querySelectorAll("tr[data-item-idx]");
    rows.forEach(function (r) { r.classList.remove("table-active"); });
    if (state.highlighted >= 0 && rows[state.highlighted]) {
      rows[state.highlighted].classList.add("table-active");
      rows[state.highlighted].scrollIntoView({ block: "nearest" });
    }
  }

  function selectRow(idx) {
    var item = state.rows[idx];
    if (!item) return;
    var cb = state.onSelect;
    bsModal.hide();
    if (typeof cb === "function") cb(item);
  }

  /** Open the Universal Item Lookup.
   * options.onSelect(item) — called once, with the chosen row.
   * options.categoryId — optional initial category filter. */
  function open(options) {
    options = options || {};
    buildModalOnce();
    state = {
      onSelect: options.onSelect,
      page: 1, total: 0, totalPages: 1,
      rows: [], highlighted: -1,
      categoryId: options.categoryId || null,
      filterValues: {},
    };
    els.search.value = "";
    els.filters.innerHTML = "";
    els.results.innerHTML = "";
    els.pageInfo.textContent = "";
    if (els.category.options.length <= 1) {
      loadCategories().then(function () {
        if (state.categoryId) els.category.value = state.categoryId;
        if (state.categoryId) loadFilterFields(state.categoryId);
      });
    } else if (state.categoryId) {
      els.category.value = state.categoryId;
      loadFilterFields(state.categoryId);
    }
    bsModal.show();
    runSearch();
  }

  window.ItemLookup = { open: open };
})();
