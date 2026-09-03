"""Built-in Business Category catalog — data, not code.

Each entry is (name, slug, description, icon, priority, [field specs]).
A field spec is a dict matching ProductField's columns. This module only
describes the default catalog; ensure_builtin_categories() is what actually
writes it to the database, idempotently, via direct ORM inserts so the
normal add/edit/delete admin APIs stay the single way fields ever change
after seeding.

Field choices follow common retail/wholesale ERP master-data practice: a
small set of the attributes buyers and staff actually search or filter by
for that trade — not every attribute a product could theoretically have.
SKU is intentionally NOT declared here: it is a real Item.sku column (see
models.py), not a per-category field, so it applies uniformly to every
category and is never duplicated or shadowed by a ProductField of the same
name (see PROTECTED_FIELD_NAMES below and RESERVED_ITEM_FIELD_NAMES in
inventory/routes.py).
"""

CATEGORIES = [
    {
        "name": "General / Other", "slug": "general", "icon": "bi-box-seam", "priority": 0,
        "description": "Items that don't fit a specific industry category",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
        ],
    },
    {
        "name": "Grocery / FMCG", "slug": "grocery-fmcg", "icon": "bi-bag-check", "priority": 1,
        "description": "Packaged food, household consumables and fast-moving goods",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "pack_size", "field_label": "Pack Size", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "weight", "field_label": "Weight", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 2},
            {"field_name": "flavor", "field_label": "Flavor", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 3},
            {"field_name": "batch_no", "field_label": "Batch No.", "field_type": "text", "tab_name": "Batch & Expiry",
             "is_searchable": True, "is_filterable": True, "position": 4},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 5},
            {"field_name": "manufacturer", "field_label": "Manufacturer", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 6},
        ],
    },
    {
        "name": "Medical / Pharmacy", "slug": "medical-pharmacy", "icon": "bi-capsule", "priority": 2,
        "description": "Medicines and pharmaceutical products",
        "fields": [
            {"field_name": "generic_name", "field_label": "Generic Name", "field_type": "text", "tab_name": "General",
             "is_required": True, "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "strength", "field_label": "Strength", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "dosage_form", "field_label": "Dosage Form", "field_type": "select", "tab_name": "General",
             "is_filterable": True, "position": 3,
             "options": ["Tablet", "Capsule", "Syrup", "Injection", "Cream", "Drops", "Other"]},
            {"field_name": "pack_size", "field_label": "Pack Size", "field_type": "text", "tab_name": "General",
             "position": 4},
            {"field_name": "manufacturer", "field_label": "Manufacturer", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 5},
            {"field_name": "batch_no", "field_label": "Batch No.", "field_type": "text", "tab_name": "Batch & Expiry",
             "is_searchable": True, "is_filterable": True, "position": 6},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 7},
        ],
    },
    {
        "name": "Garments / Apparel", "slug": "garments-apparel", "icon": "bi-bag-heart", "priority": 3,
        "description": "Clothing and apparel",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "style_no", "field_label": "Style No.", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "size", "field_label": "Size", "field_type": "select", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2,
             "options": ["XS", "S", "M", "L", "XL", "XXL", "Free Size"]},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 3},
            {"field_name": "fabric", "field_label": "Fabric", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 4},
            {"field_name": "gender", "field_label": "Gender", "field_type": "select", "tab_name": "Variant",
             "is_filterable": True, "position": 5, "options": ["Men", "Women", "Unisex", "Kids"]},
            {"field_name": "season", "field_label": "Season", "field_type": "select", "tab_name": "Variant",
             "is_filterable": True, "position": 6, "options": ["Summer", "Winter", "All Season"]},
            {"field_name": "pattern", "field_label": "Pattern/Design", "field_type": "text", "tab_name": "Variant",
             "position": 7},
            {"field_name": "collection", "field_label": "Collection", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 8},
        ],
    },
    {
        "name": "Fabric / Textile", "slug": "fabric-textile", "icon": "bi-layers", "priority": 4,
        "description": "Cloth and textile material sold by length/weight",
        "fields": [
            {"field_name": "fabric_type", "field_label": "Fabric Type", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "pattern", "field_label": "Pattern", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 3},
            {"field_name": "width", "field_label": "Width", "field_type": "text", "tab_name": "Specification",
             "position": 4},
            {"field_name": "gsm", "field_label": "GSM", "field_type": "number", "tab_name": "Specification",
             "is_filterable": True, "position": 5},
            {"field_name": "composition", "field_label": "Composition", "field_type": "text", "tab_name": "Specification",
             "position": 6},
        ],
    },
    {
        "name": "Footwear", "slug": "footwear", "icon": "bi-boot", "priority": 5,
        "description": "Shoes and footwear",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "size", "field_label": "Size", "field_type": "select", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 1,
             "options": ["XS", "S", "M", "L", "XL", "XXL", "Free Size"]},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "material", "field_label": "Material", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 3},
            {"field_name": "gender", "field_label": "Gender", "field_type": "select", "tab_name": "Variant",
             "is_filterable": True, "position": 4, "options": ["Men", "Women", "Unisex", "Kids"]},
            {"field_name": "style", "field_label": "Style", "field_type": "text", "tab_name": "General",
             "position": 5},
        ],
    },
    {
        "name": "Electronics", "slug": "electronics", "icon": "bi-lightning-charge", "priority": 6,
        "description": "Electronic devices and appliances",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "serial_number", "field_label": "Serial Number", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 2},
            {"field_name": "warranty", "field_label": "Warranty", "field_type": "text", "tab_name": "General",
             "position": 3},
            {"field_name": "voltage", "field_label": "Voltage", "field_type": "text", "tab_name": "Specification",
             "position": 4},
            {"field_name": "power_watt", "field_label": "Power/Wattage", "field_type": "text", "tab_name": "Specification",
             "position": 5},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 6},
        ],
    },
    {
        "name": "Mobile Phones", "slug": "mobile-phones", "icon": "bi-phone", "priority": 7,
        "description": "Mobile phones and accessories",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "imei", "field_label": "IMEI", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 2},
            {"field_name": "ram", "field_label": "RAM", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 3},
            {"field_name": "storage", "field_label": "Storage", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 4},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 5},
            {"field_name": "warranty", "field_label": "Warranty", "field_type": "text", "tab_name": "General",
             "position": 6},
        ],
    },
    {
        "name": "Computers / IT", "slug": "computers-it", "icon": "bi-pc-display", "priority": 8,
        "description": "Computers, laptops and IT equipment",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "serial_number", "field_label": "Serial Number", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 2},
            {"field_name": "processor", "field_label": "Processor", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 3},
            {"field_name": "ram", "field_label": "RAM", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 4},
            {"field_name": "storage", "field_label": "Storage", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 5},
            {"field_name": "warranty", "field_label": "Warranty", "field_type": "text", "tab_name": "General",
             "position": 6},
        ],
    },
    {
        "name": "Electrical", "slug": "electrical", "icon": "bi-plug", "priority": 9,
        "description": "Electrical fittings, wiring and accessories",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "voltage", "field_label": "Voltage", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 2},
            {"field_name": "power_watt", "field_label": "Power/Wattage", "field_type": "text", "tab_name": "Specification",
             "position": 3},
            {"field_name": "warranty", "field_label": "Warranty", "field_type": "text", "tab_name": "General",
             "position": 4},
        ],
    },
    {
        "name": "Hardware", "slug": "hardware", "icon": "bi-hammer", "priority": 10,
        "description": "Hardware, tools and fittings",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "material", "field_label": "Material", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 1},
            {"field_name": "size", "field_label": "Size", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 2},
        ],
    },
    {
        "name": "Auto Parts", "slug": "auto-parts", "icon": "bi-car-front", "priority": 11,
        "description": "Vehicle spare parts and accessories",
        "fields": [
            {"field_name": "part_number", "field_label": "Part Number", "field_type": "text", "tab_name": "General",
             "is_required": True, "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "oem_number", "field_label": "OEM Number", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 2},
            {"field_name": "vehicle_make", "field_label": "Vehicle Make", "field_type": "text", "tab_name": "Compatibility",
             "is_filterable": True, "position": 3},
            {"field_name": "vehicle_model", "field_label": "Vehicle Model", "field_type": "text", "tab_name": "Compatibility",
             "is_filterable": True, "position": 4},
            {"field_name": "model_year", "field_label": "Model Year", "field_type": "text", "tab_name": "Compatibility",
             "is_filterable": True, "position": 5},
        ],
    },
    {
        "name": "Cosmetics / Beauty", "slug": "cosmetics-beauty", "icon": "bi-heart-fill", "priority": 12,
        "description": "Beauty and personal-care products",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "shade", "field_label": "Shade/Color", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 1},
            {"field_name": "pack_size", "field_label": "Pack Size", "field_type": "text", "tab_name": "General",
             "position": 2},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 3},
        ],
    },
    {
        "name": "Furniture", "slug": "furniture", "icon": "bi-house-door", "priority": 13,
        "description": "Home and office furniture",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "material", "field_label": "Material", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 1},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "dimensions", "field_label": "Dimensions", "field_type": "text", "tab_name": "Specification",
             "position": 3},
        ],
    },
    {
        "name": "Books / Stationery", "slug": "books-stationery", "icon": "bi-pencil-square", "priority": 14,
        "description": "Office, school supplies, books and stationery",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "author_publisher", "field_label": "Author/Publisher", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 1},
        ],
    },
    {
        "name": "Construction Materials", "slug": "construction-materials", "icon": "bi-bricks", "priority": 15,
        "description": "Building and construction materials",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "grade", "field_label": "Grade", "field_type": "text", "tab_name": "Specification",
             "is_filterable": True, "position": 1},
            {"field_name": "size", "field_label": "Size", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 2},
        ],
    },
    {
        "name": "Chemicals", "slug": "chemicals", "icon": "bi-droplet-half", "priority": 16,
        "description": "Industrial and household chemicals",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "concentration", "field_label": "Concentration", "field_type": "text", "tab_name": "Specification",
             "position": 1},
            {"field_name": "batch_no", "field_label": "Batch No.", "field_type": "text", "tab_name": "Batch & Expiry",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 3},
        ],
    },
    {
        "name": "Beverages", "slug": "beverages", "icon": "bi-cup-straw", "priority": 17,
        "description": "Drinks and beverages",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "pack_size", "field_label": "Pack Size", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "flavor", "field_label": "Flavor", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 2},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 3},
        ],
    },
    {
        "name": "Food / Restaurant", "slug": "food-restaurant", "icon": "bi-cup-hot", "priority": 18,
        "description": "Restaurant and food-service items",
        "fields": [
            {"field_name": "cuisine_type", "field_label": "Cuisine Type", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 0},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 1},
        ],
    },
    {
        "name": "Agriculture", "slug": "agriculture", "icon": "bi-flower1", "priority": 19,
        "description": "Seeds, fertilizers and agricultural inputs",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "crop_type", "field_label": "Crop Type", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 1},
            {"field_name": "batch_no", "field_label": "Batch No.", "field_type": "text", "tab_name": "Batch & Expiry",
             "is_searchable": True, "is_filterable": True, "position": 2},
            {"field_name": "expiry_date", "field_label": "Expiry Date", "field_type": "date", "tab_name": "Batch & Expiry",
             "is_filterable": True, "position": 3},
        ],
    },
    {
        "name": "Sports", "slug": "sports", "icon": "bi-trophy", "priority": 20,
        "description": "Sports equipment and accessories",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "size", "field_label": "Size", "field_type": "select", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 1,
             "options": ["XS", "S", "M", "L", "XL", "XXL", "Free Size"]},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
        ],
    },
    {
        "name": "Industrial Machinery", "slug": "industrial-machinery", "icon": "bi-gear-wide-connected", "priority": 21,
        "description": "Industrial and factory machinery",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "model", "field_label": "Model", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 1},
            {"field_name": "serial_number", "field_label": "Serial Number", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "position": 2},
            {"field_name": "warranty", "field_label": "Warranty", "field_type": "text", "tab_name": "General",
             "position": 3},
            {"field_name": "power_watt", "field_label": "Power/Wattage", "field_type": "text", "tab_name": "Specification",
             "position": 4},
        ],
    },
    {
        "name": "Household", "slug": "household", "icon": "bi-house-heart", "priority": 22,
        "description": "General household items",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "material", "field_label": "Material", "field_type": "text", "tab_name": "General",
             "is_filterable": True, "position": 1},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
        ],
    },
    {
        "name": "Toys / Baby Products", "slug": "toys-baby", "icon": "bi-puzzle", "priority": 23,
        "description": "Toys and baby/kids products",
        "fields": [
            {"field_name": "brand", "field_label": "Brand", "field_type": "text", "tab_name": "General",
             "is_searchable": True, "is_filterable": True, "position": 0},
            {"field_name": "age_group", "field_label": "Age Group", "field_type": "text", "tab_name": "Variant",
             "is_filterable": True, "position": 1},
            {"field_name": "color", "field_label": "Color", "field_type": "text", "tab_name": "Variant",
             "is_searchable": True, "is_filterable": True, "position": 2},
        ],
    },
]

# Field names that are core, protected TradeFlow identifiers rather than
# ordinary category attributes — kept in sync with RESERVED_ITEM_FIELD_NAMES
# in inventory/routes.py and ConfigurationService.RESERVED_FIELD_NAMES. A
# seeded/admin-added ProductField must never use one of these names, so a
# category field can never shadow (or be shadowed by) the real Item column
# of the same name.
PROTECTED_FIELD_NAMES = {"id", "item_id", "sku", "barcode", "item_code", "name",
                         "item_type", "unit", "business_category_id", "category_id"}


def ensure_builtin_categories():
    """Create every built-in category (and its fields) that doesn't already
    exist yet, by slug. Never touches a category/field that already exists —
    an admin's edits to a built-in category's fields (add/rename/disable)
    are the source of truth from then on, this only fills in what's missing
    on a fresh database or after a version upgrade adds a new built-in
    category. Returns (categories_created, fields_created)."""
    from salpurflask.extensions import db
    from salpurflask.models.business_config import BusinessCategory, ProductField

    categories_created = 0
    fields_created = 0

    for spec in CATEGORIES:
        cat = BusinessCategory.query.filter_by(slug=spec["slug"]).first()
        if cat is None:
            # A category by this exact name may already exist under a
            # different (e.g. legacy pre-catalog) slug — name is UNIQUE at
            # the DB level, so creating a same-named row would fail with an
            # IntegrityError instead of the intended "already there, skip".
            if BusinessCategory.query.filter_by(name=spec["name"]).first() is not None:
                continue
            cat = BusinessCategory(
                name=spec["name"], slug=spec["slug"], description=spec["description"],
                icon=spec["icon"], priority=spec["priority"], color="primary", is_enabled=True,
            )
            db.session.add(cat)
            db.session.flush()
            categories_created += 1

        existing_field_names = {f.field_name for f in cat.fields}
        for field_spec in spec["fields"]:
            if field_spec["field_name"] in PROTECTED_FIELD_NAMES:
                continue
            if field_spec["field_name"] in existing_field_names:
                continue
            db.session.add(ProductField(category_id=cat.id, **field_spec))
            fields_created += 1

    db.session.commit()
    return categories_created, fields_created
