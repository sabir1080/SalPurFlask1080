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
        assert f'href="#s{n}"' in body, f"contents is missing section {n}"
        assert f'id="s{n}"' in body, f"section {n} is in the contents but not in the page"


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
