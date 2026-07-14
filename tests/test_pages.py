"""The About page and the User Manual.

Both are public and both are full of url_for() links to real reports — a renamed route
turns them into a 500 for the first person who clicks About, which is a bad way to meet
a system.
"""
from app import app as flask_app, db, User, pwd_context


def _client(role="admin", email="a@t.com"):
    db.session.add(User(name="A", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role=role))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def test_about_and_manual_render_for_a_visitor_with_no_account(appctx):
    c = flask_app.test_client()
    for path in ("/about", "/manual"):
        r = c.get(path)
        assert r.status_code == 200, path


def test_the_manual_covers_every_section_it_promises(appctx):
    """The contents list and the sections are written in two places; a section renamed in
    one and not the other leaves a link that goes nowhere."""
    c = _client()
    body = c.get("/manual").get_data(as_text=True)

    for n in range(1, 20):
        assert f'href="#s{n}"' in body, f"English contents is missing section {n}"
        assert f'id="s{n}"' in body, f"English section {n} is linked but not in the page"


def test_the_urdu_manual_does_not_fall_behind_the_english_one(appctx):
    """Two copies of the same document drift. The Urdu one is the one that will drift,
    because it is the one nobody writing a new section remembers — and a manual that
    quietly stops matching the software is worse than no manual, since the reader trusts
    it and acts on it."""
    c = _client()
    body = c.get("/manual").get_data(as_text=True)

    for n in range(1, 20):
        assert f'href="#u{n}"' in body, f"Urdu contents is missing section {n}"
        assert f'id="u{n}"' in body, f"Urdu section {n} is linked but not in the page"


def test_both_pages_offer_both_languages_and_default_to_english(appctx):
    c = _client()
    for path in ("/about", "/manual"):
        body = c.get(path).get_data(as_text=True)
        assert 'data-lang-btn="en"' in body, path
        assert 'data-lang-btn="ur"' in body, path
        assert 'data-lang-block="en"' in body, path
        assert 'data-lang-block="ur"' in body, path
        # English unless the reader has chosen otherwise: it is what the software's own
        # buttons say, so the manual names the things the reader is looking at.
        assert "=== 'ur' ? 'ur' : 'en'" in body, f"{path} does not default to English"


def test_the_manual_tells_the_truth_about_the_rules(appctx):
    """These are the promises the manual makes on the system's behalf. Each one is
    enforced by a test elsewhere; this checks the manual still says so."""
    c = _client()
    body = c.get("/manual").get_data(as_text=True)

    for promise in (
        "reversed",                       # documents are reversed, not deleted
        "weighted average",               # costing method
        "Reconciliation",                 # the report that proves the books
        "control accounts",               # AR/AP/Inventory are off limits
        "be undone",                      # the reset warning
    ):
        assert promise.lower() in body.lower(), promise


def test_about_links_to_the_manual(appctx):
    c = flask_app.test_client()
    assert "/manual" in c.get("/about").get_data(as_text=True)


def test_dark_mode_repoints_the_bootstrap_text_colours():
    """Bootstrap keys its dark mode off [data-bs-theme]. This app has its own [data-theme]
    switch and never sets Bootstrap's, so Bootstrap stays in light mode for ever and keeps
    handing out light-mode colours: .text-muted resolves to a near-black grey, which then
    gets painted onto a near-black background. It is used ~250 times in these templates.

    Nothing errors. The text is simply almost invisible, and only in dark mode — which is
    why it survived this long.
    """
    import re
    from pathlib import Path

    css = Path(flask_app.static_folder, "css", "style.css").read_text(encoding="utf-8")
    dark = re.search(r'\[data-theme="dark"\]\s*\{(.*?)\}', css, re.DOTALL)
    assert dark, 'no [data-theme="dark"] block in style.css'
    block = dark.group(1)

    for var in ("--bs-body-color", "--bs-secondary-color", "--bs-emphasis-color"):
        assert var in block, (
            f"{var} is not repointed in dark mode — every Bootstrap utility that uses it "
            f"will render a light-mode colour on a dark background")

    # Repointing the variable is not enough by itself. Bootstrap writes
    # `.text-muted { color: var(--bs-secondary-color) !important }`, and an !important
    # declaration is beaten only by a more specific !important — not by changing what its
    # variable resolves to, wherever anything else also declares a colour there.
    override = re.search(
        r'\[data-theme="dark"\][^{]*\.text-muted[^{]*\{([^}]*)\}', css, re.DOTALL)
    assert override and "!important" in override.group(1), (
        'style.css must override .text-muted under [data-theme="dark"] with !important — '
        "repointing --bs-secondary-color alone does not beat Bootstrap's own !important")

    # And the cell *background*. Bootstrap paints every cell `var(--bs-table-bg)`, which it
    # declares as `var(--bs-body-bg)` — white, since it is stuck in light mode. Light text on
    # a white cell is not faint, it is gone. The variable has to be set on `.table` itself:
    # `.table` re-declares it on itself, so a value inherited from the theme block is thrown
    # away before it is ever read.
    table = re.search(r'\[data-theme="dark"\]\s+\.table\s*\{([^}]*)\}', css)
    assert table and "--bs-table-bg" in table.group(1), (
        'style.css must set --bs-table-bg on `[data-theme="dark"] .table` — otherwise every '
        "table cell keeps Bootstrap's white background and the dark-theme text disappears "
        "into it")


def test_money_columns_are_never_allowed_to_fold_onto_a_second_line():
    """"1,234,567.89" broken after a comma reads as two different figures, and these are
    documents that go to customers and suppliers.

    An invoice's items table used to be `table-layout: fixed` with break-word on every
    cell, so a figure too wide for its column was simply cut in half and continued
    underneath. Money is always right-aligned here, so .text-end is the handle: wherever a
    stylesheet lets cells break, the right-aligned ones must be exempt.
    """
    import re
    from pathlib import Path

    css = Path(flask_app.static_folder, "css", "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.table td\.text-end[^{]*\{[^}]*white-space:\s*nowrap", css, re.DOTALL), (
        "style.css must stop right-aligned (money) table cells from wrapping")

    for name in ("invoice_sale.html", "invoice_purchase.html", "reports.html"):
        text = Path(flask_app.template_folder, name).read_text(encoding="utf-8")
        # Anything that permits breaking inside a word must be paired with a nowrap rule
        # for the money columns in the same file.
        if re.search(r"(overflow-wrap|word-wrap|word-break)\s*:\s*(break-word|anywhere)", text):
            assert re.search(r"white-space:\s*nowrap", text), (
                f"{name} lets cells break mid-word but never exempts the money columns")


def test_no_template_uses_a_css_variable_that_does_not_exist():
    """A misspelt CSS variable fails silently. `color: var(--muted)` — when the variable
    is actually `--text-muted` — is not an error: the browser simply has no colour to
    apply, and the text renders in whatever it inherits. It looks washed out, and nothing
    anywhere says why. That is exactly how the About page and the manual shipped
    unreadable.
    """
    import re
    from pathlib import Path

    css = Path(flask_app.static_folder, "css", "style.css").read_text(encoding="utf-8")
    defined = set(re.findall(r"^\s*(--[\w-]+)\s*:", css, re.MULTILINE))
    assert defined, "no CSS variables found — has style.css moved?"

    bad = []
    for tpl in Path(flask_app.template_folder).rglob("*.html"):
        text = tpl.read_text(encoding="utf-8")
        for used in re.findall(r"var\(\s*(--[\w-]+)", text):
            # Bootstrap defines its own (--bs-*); we only own ours.
            if used.startswith("--bs-") or used in defined:
                continue
            bad.append(f"{tpl.name}: var({used})")

    assert not bad, "CSS variables used but never defined:\n  " + "\n  ".join(sorted(set(bad)))
