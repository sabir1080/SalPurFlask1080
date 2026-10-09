"""Footwear category fields: Size (required), Color, Brand, Gender, Material, Style.

One definition (FOOTWEAR_FIELDS in services/category_catalog.py) feeds the older catalog and the boot-time
default filler. A ProductField is unique per (category, field_name) and values are stored per
(item, category, field_name), so Footwear's Size / Color / Gender never collide with Garments' own.
"""
from decimal import Decimal

from app import app as flask_app, db, User, pwd_context
from salpurflask.models.models import Item
from salpurflask.models.business_config import BusinessCategory, ProductField, ProductCategoryData
from salpurflask.services.category_catalog import (
    FOOTWEAR_FIELDS, FOOTWEAR_SIZES, CATEGORIES, ensure_default_business_categories,
    ensure_default_product_fields,
)

NAMES = ["size", "color", "brand", "gender", "material", "style"]


def _admin(email="admin@footwear.com"):
    db.session.add(User(name="Admin", email=email, password=pwd_context.hash("secret123"),
                        verified=True, role="admin"))
    db.session.commit()
    c = flask_app.test_client()
    c.post("/signin", data={"email": email, "password": "secret123"})
    return c


def _footwear():
    """A Footwear category the way the generator left it: it exists, with no fields."""
    cat = BusinessCategory(name="Footwear", slug="footwear", is_enabled=True)
    db.session.add(cat)
    db.session.commit()
    return cat


def _fields(cat):
    return {f.field_name: f for f in ProductField.query.filter_by(category_id=cat.id)}


def _form(cat, **extra):
    data = {"name": "Oxford", "business_category_id": str(cat.id), "unit": "Pair", "item_type": "STOCK",
            "opening_stock": "0", "reorder_level": "5", "purchase_price": "10", "sale_price": "20"}
    data.update(extra)
    return data


def _stored(item):
    return {d.field_name: d.field_value for d in ProductCategoryData.query.filter_by(product_id=item.id)}


# ── the definition ──────────────────────────────────────────────────────────


def test_sizes_are_uk_1_to_13_with_half_sizes():
    assert FOOTWEAR_SIZES[0] == "UK 1" and FOOTWEAR_SIZES[1] == "UK 1.5" and FOOTWEAR_SIZES[-1] == "UK 13"
    assert len(FOOTWEAR_SIZES) == 25 and len(set(FOOTWEAR_SIZES)) == 25


def test_the_six_fields_and_their_types():
    by = {f["field_name"]: f for f in FOOTWEAR_FIELDS}
    assert list(by) == NAMES
    assert {n: by[n]["field_type"] for n in NAMES} == {
        "size": "select", "color": "select", "brand": "text", "gender": "select", "material": "select",
        "style": "select"}
    assert [n for n in NAMES if by[n].get("is_required")] == ["size"]
    assert by["gender"]["options"] == ["Men", "Women", "Kids", "Unisex"]
    assert by["material"]["options"] == ["Leather", "Synthetic", "Canvas", "Textile", "Rubber", "Other"]
    assert by["style"]["options"] == ["Formal", "Casual", "Sports", "Sandals", "Slippers", "Boots", "Other"]


def test_the_older_catalog_uses_the_same_definition():
    spec = next(c for c in CATEGORIES if c["slug"] == "footwear")
    assert spec["fields"] is FOOTWEAR_FIELDS


# ── seeding an existing Footwear category ───────────────────────────────────


def test_an_existing_footwear_category_gets_the_six_fields(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    fields = _fields(cat)
    assert list(sorted(fields)) == sorted(NAMES)
    assert fields["size"].is_required is True and fields["size"].field_type == "select"
    assert fields["size"].options == FOOTWEAR_SIZES
    assert all(f.is_active for f in fields.values())


def test_seeding_is_idempotent_and_keeps_an_admins_edits(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    size = _fields(cat)["size"]
    size.field_label = "Shoe Size"
    size.is_required = False
    db.session.commit()

    ensure_default_product_fields()                           # nothing new to create for Footwear
    fields = _fields(cat)
    assert len(fields) == 6
    assert fields["size"].field_label == "Shoe Size" and fields["size"].is_required is False


def test_no_footwear_category_means_nothing_is_created(appctx):
    ensure_default_business_categories()                      # the 26 defaults ("Shoes", not "Footwear")
    assert BusinessCategory.query.filter_by(slug="footwear").count() == 0


def test_garments_and_electronics_fields_are_untouched(appctx):
    ensure_default_business_categories()
    before = {(f.category.slug, f.field_name): (f.field_label, f.field_type, f.is_required, f.options, f.is_active)
              for f in ProductField.query.all()}
    _footwear()
    ensure_default_product_fields()
    after = {(f.category.slug, f.field_name): (f.field_label, f.field_type, f.is_required, f.options, f.is_active)
             for f in ProductField.query.all() if f.category.slug != "footwear"}
    assert after == before                                    # every other category exactly as it was
    garments_size = ProductField.query.join(BusinessCategory).filter(
        BusinessCategory.slug == "garments", ProductField.field_name == "size").one()
    assert garments_size.options[:3] == ["XS", "S", "M"]      # clothing sizes, not UK sizes
    assert ProductField.query.join(BusinessCategory).filter(
        BusinessCategory.slug == "electronics", ProductField.field_name == "warranty").count() == 1


# ── Add Item / Edit Item ────────────────────────────────────────────────────


def test_add_item_form_receives_all_six_footwear_fields(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    body = _admin().get(f"/admin/config/api/category/{cat.id}/fields").get_json()
    assert [f["field_name"] for f in body] == NAMES
    size = next(f for f in body if f["field_name"] == "size")
    assert size["is_required"] is True and size["field_type"] == "select" and "UK 6.5" in size["options"]


def test_footwear_item_saves_and_reloads_its_values(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    admin = _admin()
    admin.post("/item", data=_form(cat, size="UK 8", color="Black", brand="Bata", gender="Men",
                                   material="Leather", style="Formal"), follow_redirects=True)
    item = Item.query.filter_by(name="Oxford").one()
    assert _stored(item) == {"size": "UK 8", "color": "Black", "brand": "Bata", "gender": "Men",
                             "material": "Leather", "style": "Formal"}
    # what Edit Item loads
    assert admin.get(f"/api/product-category-data/{item.id}/{cat.id}").get_json() == _stored(item)

    admin.post(f"/item/edit/{item.id}", data=_form(cat, size="UK 9.5", color="Brown", brand="Bata",
                                                   gender="Women", material="Canvas", style="Casual"),
               follow_redirects=True)
    db.session.expire_all()
    assert _stored(item) == {"size": "UK 9.5", "color": "Brown", "brand": "Bata", "gender": "Women",
                             "material": "Canvas", "style": "Casual"}


def test_size_is_required_on_the_server(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    resp = _admin().post("/item", data=_form(cat, color="Black"), follow_redirects=True)
    assert b"Size is required" in resp.data
    assert Item.query.filter_by(name="Oxford").count() == 0


def test_optional_fields_may_be_left_blank(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    _admin().post("/item", data=_form(cat, size="UK 7"), follow_redirects=True)
    assert _stored(Item.query.filter_by(name="Oxford").one()) == {"size": "UK 7"}


def test_footwear_and_garments_same_named_fields_do_not_collide(appctx):
    ensure_default_business_categories()
    garments = BusinessCategory.query.filter_by(slug="garments").one()
    cat = _footwear()
    ensure_default_product_fields()
    admin = _admin()
    admin.post("/item", data=_form(garments, name="Shirt", size="M", color="Blue", gender="Men"),
               follow_redirects=True)
    admin.post("/item", data=_form(cat, name="Boot", size="UK 10", color="Tan", gender="Kids"),
               follow_redirects=True)
    assert _stored(Item.query.filter_by(name="Shirt").one()) == {"size": "M", "color": "Blue", "gender": "Men"}
    boot = _stored(Item.query.filter_by(name="Boot").one())
    assert boot["size"] == "UK 10" and boot["color"] == "Tan" and boot["gender"] == "Kids"


def test_footwear_batch_and_expiry_follow_the_category_settings(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    db.session.refresh(cat)
    assert (cat.batch_tracking_enabled, cat.stock_expiry_enabled) == (False, False)
    cat.config_data = {"batch_tracking": True}
    db.session.commit()
    assert (cat.batch_tracking_enabled, cat.stock_expiry_enabled) == (True, False)


# ── Edit Item must show the saved selections ────────────────────────────────


def _edit_item_render_field_source():
    with open("templates/edit_item.html", encoding="utf-8") as f:
        src = f.read()
    start = src.index("function renderField(")
    return src[start:src.index("// Clear saved data on successful submission", start)]


def test_edit_item_renderer_selects_the_saved_dropdown_value():
    """Edit Item's category-field renderer used to build every <option> without `selected`, so a saved Size /
    Gender / Material / Style came back as the "Select ..." placeholder (the data was saved fine). The JS itself
    is exercised in a browser engine; this pins that the markup is generated from the saved value."""
    body = _edit_item_render_field_source()
    select_case = body[body.index("case 'select':"):body.index("case 'boolean':")]
    assert "currentValue" in select_case and "selected" in select_case
    assert "<option value=\"\">Select ${fieldLabel}...</option>" in select_case    # placeholder only when unset


def test_edit_item_loads_the_saved_values_for_its_own_category(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    admin = _admin()
    admin.post("/item", data=_form(cat, size="UK 11", gender="Kids", material="Rubber", style="Sandals",
                                   color="Red", brand="Servis"), follow_redirects=True)
    item = Item.query.filter_by(name="Oxford").one()
    html = admin.get(f"/item/edit/{item.id}").get_data(as_text=True)
    assert f"/api/product-category-data/${{ITEM_ID}}/${{categoryId}}" in html           # the loader Edit Item uses
    assert admin.get(f"/api/product-category-data/{item.id}/{cat.id}").get_json() == {
        "size": "UK 11", "gender": "Kids", "material": "Rubber", "style": "Sandals",
        "color": "Red", "brand": "Servis"}


# ── Style: text -> dropdown ─────────────────────────────────────────────────

STYLE_OPTIONS = ["Formal", "Casual", "Sports", "Sandals", "Slippers", "Boots", "Other"]


def _legacy_style(cat, **kw):
    """The Footwear 'style' field as an admin created it by hand: a plain text field."""
    spec = dict(category_id=cat.id, field_name="style", field_label="style", field_type="text",
                tab_name="General", position=0, is_required=False, is_system_default=False)
    spec.update(kw)
    f = ProductField(**spec)
    db.session.add(f)
    db.session.commit()
    return f


def test_only_footwear_defines_a_style_field_in_the_catalogs():
    from salpurflask.services.category_catalog import DEFAULT_PRODUCT_FIELDS
    owners = [c["slug"] for c in CATEGORIES for f in c["fields"] if f["field_name"] == "style"]
    owners += [slug for slug, specs in DEFAULT_PRODUCT_FIELDS.items() for f in specs if f["field_name"] == "style"]
    assert owners == ["footwear"]
    assert next(f for f in FOOTWEAR_FIELDS if f["field_name"] == "style")["options"] == STYLE_OPTIONS


def test_a_plain_text_style_field_is_upgraded_in_place_keeping_its_row_and_values(appctx):
    cat = _footwear()
    old = _legacy_style(cat)
    old_id = old.id
    item = Item(name="Old Shoe", business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=0,
                opening_stock=0, purchase_price=1, sale_price=2)
    db.session.add(item)
    db.session.flush()
    db.session.add(ProductCategoryData(product_id=item.id, category_id=cat.id, field_name="style",
                                       field_value="Latest"))
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()

    fields = _fields(cat)
    style = fields["style"]
    assert style.id == old_id and len(ProductField.query.filter_by(category_id=cat.id, field_name="style").all()) == 1
    assert style.field_type == "select" and style.options == STYLE_OPTIONS
    # it moves from General to the Variant tab (last, after Material); label and required flag are kept
    assert (style.tab_name, style.position) == ("Variant", 5)
    assert (style.field_label, style.is_required) == ("style", False)
    assert style.is_system_default is True                     # marks it as upgraded, so it is not redone
    # the other five arrive as before, and the saved value is untouched
    assert sorted(fields) == sorted(NAMES)
    assert _stored(item) == {"style": "Latest"}


def test_the_upgrade_runs_once_and_never_overrides_an_admins_changes(appctx):
    cat = _footwear()
    _legacy_style(cat)
    ensure_default_product_fields()
    style = _fields(cat)["style"]
    style.options = ["Dress", "Work"]                    # the admin then sets their own list
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()
    assert _fields(cat)["style"].options == ["Dress", "Work"]


def test_a_style_field_with_options_or_another_type_is_not_touched(appctx):
    cat = _footwear()
    _legacy_style(cat, field_type="text", options=["Mine"])        # already customised
    ensure_default_product_fields()
    style = _fields(cat)["style"]
    assert style.field_type == "text" and style.options == ["Mine"]

    other = BusinessCategory(name="Hats", slug="hats", is_enabled=True)
    db.session.add(other)
    db.session.commit()
    _legacy_style(other)                                            # a text Style in another category
    ensure_default_product_fields()
    assert _fields(other)["style"].field_type == "text"
    assert _fields(other)["style"].options in (None, [])


def test_garments_style_no_and_every_other_category_stay_text(appctx):
    ensure_default_business_categories()
    cat = _footwear()
    _legacy_style(cat)
    before = {(f.category.slug, f.field_name): (f.field_type, f.options) for f in ProductField.query.all()
              if f.category.slug not in ("footwear",)}
    ensure_default_product_fields()
    after = {(f.category.slug, f.field_name): (f.field_type, f.options) for f in ProductField.query.all()
             if f.category.slug not in ("footwear",)}
    assert before == after
    assert not any(k[1] == "style" for k in after)


def test_style_dropdown_value_saves_updates_and_a_legacy_value_survives_a_resave(appctx):
    cat = _footwear()
    _legacy_style(cat)
    ensure_default_product_fields()
    admin = _admin()
    admin.post("/item", data=_form(cat, size="UK 8", style="Sandals"), follow_redirects=True)
    item = Item.query.filter_by(name="Oxford").one()
    assert _stored(item)["style"] == "Sandals"

    admin.post(f"/item/edit/{item.id}", data=_form(cat, size="UK 8", style="Boots"), follow_redirects=True)
    db.session.expire_all()
    assert _stored(item)["style"] == "Boots"

    # a value saved before the field became a dropdown ("New" is not an option) is kept when the item is re-saved
    ProductCategoryData.query.filter_by(product_id=item.id, field_name="style").one().field_value = "New"
    db.session.commit()
    assert admin.get(f"/api/product-category-data/{item.id}/{cat.id}").get_json()["style"] == "New"
    admin.post(f"/item/edit/{item.id}", data=_form(cat, size="UK 8", style="New"), follow_redirects=True)
    db.session.expire_all()
    assert _stored(item)["style"] == "New"


def test_a_style_field_already_upgraded_to_a_dropdown_but_still_on_general_is_moved_to_variant(appctx):
    """The real cause of 'Style is still under General in Chrome': an earlier boot had already turned the
    hand-made text field into a dropdown (type + options) WITHOUT moving it. The upgrade only looked for a
    plain text field, so on every later boot the row no longer matched and its tab stayed General / position 0."""
    cat = _footwear()
    old = _legacy_style(cat, field_type="select", options=list(STYLE_OPTIONS))      # exactly the live state
    old_id = old.id
    items = []
    for n, value in enumerate(("New", "Latest", "Sports")):
        it = Item(name=f"Shoe {n}", business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=0,
                  opening_stock=0, purchase_price=1, sale_price=2)
        db.session.add(it)
        db.session.flush()
        db.session.add(ProductCategoryData(product_id=it.id, category_id=cat.id, field_name="style",
                                           field_value=value))
        items.append(it)
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()

    styles = ProductField.query.filter_by(category_id=cat.id, field_name="style").all()
    assert len(styles) == 1                                    # no duplicate
    style = styles[0]
    assert style.id == old_id
    assert (style.tab_name, style.position) == ("Variant", 5)
    assert style.field_type == "select" and style.options == STYLE_OPTIONS
    assert (style.field_label, style.is_required) == ("style", False)
    assert [_stored(i)["style"] for i in items] == ["New", "Latest", "Sports"]
    # Variant order: Size, Color, Gender, Material, Style
    variant = [f.field_name for f in sorted(_fields(cat).values(), key=lambda f: f.position)
               if f.tab_name == "Variant"]
    assert variant == ["size", "color", "gender", "material", "style"]


def test_the_move_happens_once_and_an_admins_placement_is_respected(appctx):
    cat = _footwear()
    _legacy_style(cat, field_type="select", options=list(STYLE_OPTIONS))
    ensure_default_product_fields()
    style = _fields(cat)["style"]
    style.tab_name, style.position = "General", 0                  # the admin puts it back
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()
    assert (_fields(cat)["style"].tab_name, _fields(cat)["style"].position) == ("General", 0)


def test_a_dropdown_style_with_the_admins_own_options_is_not_moved(appctx):
    cat = _footwear()
    _legacy_style(cat, field_type="select", options=["Dress", "Work"])
    ensure_default_product_fields()
    style = _fields(cat)["style"]
    assert (style.tab_name, style.position, style.options) == ("General", 0, ["Dress", "Work"])


def test_a_dropdown_style_the_admin_already_placed_elsewhere_is_not_moved(appctx):
    cat = _footwear()
    _legacy_style(cat, field_type="select", options=list(STYLE_OPTIONS), tab_name="Details", position=3)
    ensure_default_product_fields()
    style = _fields(cat)["style"]
    assert (style.tab_name, style.position) == ("Details", 3)


# ── Color: text -> dropdown ─────────────────────────────────────────────────

COLOR_OPTIONS = ["Black", "White", "Brown", "Tan", "Blue", "Navy Blue", "Red", "Green", "Grey", "Beige",
                 "Pink", "Yellow", "Orange", "Purple", "Gold", "Silver", "Multicolor", "Other"]
COLOR_LEDGER = "field_upgrade:footwear:color"


def _seeded_text_color(cat, **kw):
    """Footwear Color as the first version of the defaults created it: a system-default text field."""
    spec = dict(category_id=cat.id, field_name="color", field_label="Color", field_type="text",
                tab_name="Variant", position=1, is_required=False, is_system_default=True)
    spec.update(kw)
    f = ProductField(**spec)
    db.session.add(f)
    db.session.commit()
    return f


def _ledger():
    from salpurflask.models import AppConfiguration
    return AppConfiguration.get_value(COLOR_LEDGER)


def test_color_is_a_dropdown_with_the_requested_options():
    color = next(f for f in FOOTWEAR_FIELDS if f["field_name"] == "color")
    assert color["field_type"] == "select" and color["options"] == COLOR_OPTIONS
    assert not color.get("is_required")


def test_a_new_footwear_category_gets_the_color_dropdown(appctx):
    cat = _footwear()
    ensure_default_product_fields()
    color = _fields(cat)["color"]
    assert color.field_type == "select" and color.options == COLOR_OPTIONS
    body = _admin().get(f"/admin/config/api/category/{cat.id}/fields").get_json()
    assert next(f for f in body if f["field_name"] == "color")["options"] == COLOR_OPTIONS


def test_an_untouched_text_color_is_upgraded_in_place_keeping_everything_else(appctx):
    cat = _footwear()
    old = _seeded_text_color(cat)
    old_id = old.id
    items = []
    for n, value in enumerate(("Black", "Maroon", "Mehndi")):          # the last two are not in the new list
        it = Item(name=f"Shoe {n}", business_category_id=cat.id, unit="Pcs", item_type="STOCK", stock=0,
                  opening_stock=0, purchase_price=1, sale_price=2)
        db.session.add(it)
        db.session.flush()
        db.session.add(ProductCategoryData(product_id=it.id, category_id=cat.id, field_name="color",
                                           field_value=value))
        items.append(it)
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()

    colors = ProductField.query.filter_by(category_id=cat.id, field_name="color").all()
    assert len(colors) == 1                                          # no duplicate
    color = colors[0]
    assert color.id == old_id and color.field_type == "select" and color.options == COLOR_OPTIONS
    assert (color.field_label, color.is_required, color.tab_name, color.position) == ("Color", False, "Variant", 1)
    assert [_stored(i)["color"] for i in items] == ["Black", "Maroon", "Mehndi"]       # saved values untouched
    assert _ledger() == "done"


def test_saved_colors_outside_the_list_are_kept_when_the_item_is_edited(appctx):
    cat = _footwear()
    _seeded_text_color(cat)
    ensure_default_product_fields()
    admin = _admin()
    admin.post("/item", data=_form(cat, size="UK 8", color="Black"), follow_redirects=True)
    item = Item.query.filter_by(name="Oxford").one()
    _stored_row = ProductCategoryData.query.filter_by(product_id=item.id, field_name="color").one()
    _stored_row.field_value = "Maroon"                              # saved back when Color was free text
    db.session.commit()
    assert admin.get(f"/api/product-category-data/{item.id}/{cat.id}").get_json()["color"] == "Maroon"
    # Edit Item shows it (the renderer keeps a saved value that is not an option) and re-saving keeps it
    assert "choices" in _edit_item_render_field_source()
    admin.post(f"/item/edit/{item.id}", data=_form(cat, size="UK 8", color="Maroon"), follow_redirects=True)
    db.session.expire_all()
    assert _stored(item)["color"] == "Maroon"
    admin.post(f"/item/edit/{item.id}", data=_form(cat, size="UK 8", color="Navy Blue"), follow_redirects=True)
    db.session.expire_all()
    assert _stored(item)["color"] == "Navy Blue"


def test_an_admins_own_color_dropdown_is_not_overwritten(appctx):
    cat = _footwear()
    _seeded_text_color(cat, field_type="select", options=["Dark", "Light"])
    ensure_default_product_fields()
    color = _fields(cat)["color"]
    assert color.field_type == "select" and color.options == ["Dark", "Light"]


def test_a_text_color_with_options_or_another_type_is_not_overwritten(appctx):
    cat = _footwear()
    _seeded_text_color(cat, options=["Mine"])
    ensure_default_product_fields()
    assert (_fields(cat)["color"].field_type, _fields(cat)["color"].options) == ("text", ["Mine"])


def test_a_color_the_admin_changes_back_to_text_is_not_upgraded_again(appctx):
    cat = _footwear()
    _seeded_text_color(cat)
    ensure_default_product_fields()                                  # upgraded once
    color = _fields(cat)["color"]
    color.field_type, color.options = "text", None                  # the admin decides on free text after all
    db.session.commit()

    ensure_default_product_fields()
    db.session.expire_all()
    assert (_fields(cat)["color"].field_type, _fields(cat)["color"].options) == ("text", None)


def test_color_in_other_categories_stays_text(appctx):
    ensure_default_business_categories()
    before = {(f.category.slug, f.field_name): (f.field_type, f.options) for f in ProductField.query.all()}
    cat = _footwear()
    _seeded_text_color(cat)
    ensure_default_product_fields()
    after = {(f.category.slug, f.field_name): (f.field_type, f.options) for f in ProductField.query.all()
             if f.category.slug != "footwear"}
    assert after == before
    assert before[("garments", "color")][0] == "text" and before[("shoes", "color")][0] == "text"
