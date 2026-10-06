/* Put a form back the way the user filled it in after a POST failed.
 *
 * base.html embeds #form-restore-data (built by salpurflask/utils/form_restore.py)
 * only on a POST response that carries an error/warning and whose route does not
 * already pass form_data. It holds request.form as submitted, plus the names the
 * pickers show for hidden ids and, per item row, that item's units / stock /
 * batch flag. This script finds the form that was submitted, adds or removes
 * line-item rows to match, and refills every field. It never submits anything.
 */
(function () {
    'use strict';

    var node = document.getElementById('form-restore-data');
    if (!node) return;
    var data;
    try { data = JSON.parse(node.textContent); } catch (e) { return; }

    var fields = data.fields || {};
    var labels = data.labels || {};
    var items = data.items || [];

    function esc(name) { return name.replace(/\\/g, '\\\\').replace(/"/g, '\\"'); }
    function named(scope, name) {
        return Array.prototype.slice.call(scope.querySelectorAll('[name="' + esc(name) + '"]'));
    }
    function isList(name) { return /\[\]$/.test(name); }
    function isToggle(el) { return el.type === 'checkbox' || el.type === 'radio'; }

    // The submitted form is the POST form on the page that shares the most field
    // names with what was submitted (the page may also hold search / delete forms).
    function pickForm() {
        var best = null, bestScore = 0;
        Array.prototype.forEach.call(document.forms, function (f) {
            if ((f.getAttribute('method') || 'get').toLowerCase() !== 'post') return;
            var score = 0;
            Object.keys(fields).forEach(function (n) { if (named(f, n).length) score++; });
            if (score > bestScore) { best = f; bestScore = score; }
        });
        return best;
    }

    function clearRow(row) {
        Array.prototype.forEach.call(row.querySelectorAll('input, textarea'), function (el) {
            if (!isToggle(el)) el.value = '';
        });
    }

    // Line-item rows: make the number of rows equal the number submitted. The
    // page's own addRow() is used when it has one (it knows its row markup);
    // otherwise the last row is cloned.
    function syncRows(form) {
        var key = null, most = 0;
        Object.keys(fields).forEach(function (n) {
            if (!isList(n)) return;
            var els = named(form, n);
            if (!els.length || els.every(isToggle)) return;
            if (fields[n].length > most) { most = fields[n].length; key = n; }
        });
        if (!key) return;

        var guard = most + 5;
        while (named(form, key).length < most && guard-- > 0) {
            var before = named(form, key).length;
            if (typeof window.addRow === 'function') {
                try { window.addRow(); } catch (e) { /* fall through to clone */ }
            }
            if (named(form, key).length === before) {
                var rows = named(form, key).map(function (e) { return e.closest('tr'); }).filter(Boolean);
                var last = rows[rows.length - 1];
                if (!last) break;
                var copy = last.cloneNode(true);
                clearRow(copy);
                last.parentNode.appendChild(copy);
            }
        }
        guard = most + 5;
        while (named(form, key).length > most && guard-- > 0) {
            var surplus = named(form, key).pop().closest('tr');
            if (!surplus) break;
            surplus.parentNode.removeChild(surplus);
        }
    }

    function hasOption(sel, v) {
        return Array.prototype.some.call(sel.options, function (o) { return o.value === v; });
    }

    // A select whose options arrive later (fetched per item / per supplier) gets
    // its value as soon as the matching option shows up.
    function setSelect(sel, v) {
        if (hasOption(sel, v)) { sel.value = v; return; }
        if (typeof MutationObserver === 'undefined') return;
        var mo = new MutationObserver(function () {
            if (hasOption(sel, v)) { sel.value = v; mo.disconnect(); }
        });
        mo.observe(sel, { childList: true });
        setTimeout(function () { mo.disconnect(); }, 8000);
    }

    function unitsFor(form, el) {
        var idEls = named(form, 'item_id[]');
        if (!idEls.length) idEls = named(form, 'item_id');
        var row = el.closest('tr');
        var idx = -1;
        idEls.forEach(function (e, i) { if (row && row.contains(e)) idx = i; });
        return idx >= 0 && items[idx] ? items[idx].units || [] : [];
    }

    function fillUnits(form, sel, v, itemChanged) {
        var units = unitsFor(form, sel);
        // Edit/correct pages re-fetch each row's units on load and keep the unit
        // whose *name* is in data-existing-unit (leaving the price alone): point
        // that at the submitted unit.
        units.forEach(function (u) { if (String(u.key) === v) sel.setAttribute('data-existing-unit', u.name); });
        if (itemChanged) sel.innerHTML = '';
        if (sel.options.length === 0 && units.length) {
            var priceField = named(form, 'purchase_price[]').length ? 'purchase_price' : 'sale_price';
            units.forEach(function (u) {
                var opt = document.createElement('option');
                opt.value = u.key;
                opt.dataset.factor = u.factor;
                if (u[priceField] !== null && u[priceField] !== undefined) opt.dataset.price = u[priceField];
                opt.textContent = u.name;
                sel.appendChild(opt);
            });
        }
        setSelect(sel, v);
    }

    function setLabel(el, text) {
        if (!text) return;
        var scope = el.closest('td') || el.parentElement;
        var span = scope && scope.querySelector('[class*="picker-label"], [id$="_label"]');
        if (span) { span.textContent = text; span.classList.remove('text-muted'); }
    }

    function restore(form) {
        var touched = [];

        // Rows already on the page (edit/correct pages are rendered from the saved
        // document) whose item is not the one submitted: their stock hint and unit
        // list belong to the old item and must be replaced, not kept.
        var itemField = named(form, 'item_id[]').length ? 'item_id[]' : 'item_id';
        var changed = {};
        named(form, itemField).forEach(function (el, i) {
            var submitted = (fields[itemField] || [])[i];
            if (el.type === 'hidden' && submitted !== undefined && el.value !== submitted) changed[i] = true;
        });

        Object.keys(fields).forEach(function (name) {
            var vals = fields[name];
            named(form, name).forEach(function (el, i) {
                var v = isList(name) ? vals[i] : vals[0];
                if (isToggle(el)) { el.checked = vals.indexOf(el.value) !== -1; return; }
                if (v === undefined) return;
                var t = (el.type || '').toLowerCase();
                if (t === 'file' || t === 'password' || t === 'submit' || t === 'button') return;
                if (el.tagName === 'SELECT') {
                    if (el.classList.contains('unit-select') || name === 'unit_id[]') fillUnits(form, el, v, !!changed[i]);
                    else setSelect(el, v);
                } else {
                    el.value = v;
                }
                touched.push(el);
            });
        });

        // An unticked box is simply absent from the submission.
        Array.prototype.forEach.call(form.querySelectorAll('input[type="checkbox"][name]'), function (cb) {
            if (!(cb.name in fields)) cb.checked = false;
        });

        // Names the pickers show for the hidden ids.
        Object.keys(labels).forEach(function (name) {
            var text = labels[name];
            named(form, name).forEach(function (el, i) {
                setLabel(el, Array.isArray(text) ? text[i] : text);
            });
        });
        var idEls = named(form, 'item_id[]');
        if (!idEls.length) idEls = named(form, 'item_id').filter(function (e) { return e.type === 'hidden'; });
        idEls.forEach(function (el, i) {
            var meta = items[i];
            if (!meta || el.type !== 'hidden') return;
            setLabel(el, meta.name);
            var row = el.closest('tr');
            var stock = row && row.querySelector('.stock-display');
            if (stock && (changed[i] || /^[\s—-]*$/.test(stock.textContent))) stock.textContent = 'Stock: ' + meta.stock;
            if (row && meta.batch_tracked) row.classList.add('batch-tracked-row');
        });
        if (typeof window.updateBatchColumnVisibility === 'function') window.updateBatchColumnVisibility();

        // Let the page's own handlers recompute totals / dependent fields -- but
        // never a unit change (it would overwrite the submitted price) and never a
        // handler that submits the form.
        touched.forEach(function (el) {
            var change = el.getAttribute('onchange') || '';
            if (el.tagName === 'SELECT' && change && !el.classList.contains('unit-select') &&
                change.indexOf('submit') === -1) {
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        touched.forEach(function (el) {
            if (el.getAttribute('oninput')) el.dispatchEvent(new Event('input', { bubbles: true }));
        });
        if (typeof window.calcGrand === 'function') { try { window.calcGrand(); } catch (e) { /* page-specific */ } }

        // Mark the quantity / price inputs the server rejected, so the bad row is
        // visible (the flashed message quotes the value but not the row).
        var invalid = data.invalid || {};
        Object.keys(invalid).forEach(function (name) {
            var els = named(form, name);
            invalid[name].forEach(function (i) { if (els[i]) els[i].classList.add('is-invalid'); });
        });
    }

    // Runs as soon as the page's own scripts above have defined addRow() etc. --
    // deliberately NOT on DOMContentLoaded: edit/correct pages re-fetch each row's
    // units on that event and read data-existing-unit at that moment, so the
    // submitted unit has to be in place before it fires (otherwise the fetched
    // unit's default price overwrites the price the user typed).
    var form = pickForm();
    if (!form || form.dataset.formRestored) return;
    form.dataset.formRestored = '1';
    syncRows(form);
    restore(form);
}());
