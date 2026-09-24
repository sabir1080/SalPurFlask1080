"""Demo medicine catalog for the batch-tracked/FEFO stage of the test-data
generator (see generate_test_data.py's stage11_medical_batch_items).

Pure data, no imports from the app — kept separate from generate_test_data.py
so the catalog can be read/extended without wading through generator logic.

Each row: (name, generic_name, manufacturer, dosage_form, pack_size,
           purchase_price, sale_price)

Names, manufacturers and prices are demo values for a realistic Pakistani
medical-store demo — not a copy of any single real manufacturer's catalog or
real pricing. `dosage_form` matches the existing "dosage_form"/"Form" product
field's option list (Tablet/Capsule/Syrup/Injection/Cream/Drops/Other) defined
in salpurflask/services/category_catalog.py, so an item created against the
"Medical Store" BusinessCategory has a value its own custom field already
expects.

All MEDICINES rows use the single existing "Medical Store" BusinessCategory
(one of the 26 DEFAULT_BUSINESS_CATEGORIES) — no per-row category field here,
the generator looks that category up once.
"""

# (name, generic_name, manufacturer, dosage_form, pack_size, purchase_price, sale_price)
MEDICINES = [
    ("Panadol 500mg",              "Paracetamol",         "GSK Pakistan",        "Tablet",     "10s strip",   35,   55),
    ("Calpol Syrup 60ml",          "Paracetamol",         "GSK Pakistan",        "Syrup",      "60ml bottle", 85,  130),
    ("Brufen 400mg",               "Ibuprofen",           "Abbott Pakistan",     "Tablet",     "10s strip",   45,   70),
    ("Voren 50mg",                 "Diclofenac Sodium",   "Novartis Pakistan",   "Tablet",     "10s strip",   40,   65),
    ("Augmentin 625mg",            "Amoxicillin/Clavulanate", "GSK Pakistan",    "Tablet",     "6s strip",   180,  260),
    ("Amoxil 500mg",               "Amoxicillin",         "GSK Pakistan",        "Capsule",    "12s strip",   90,  140),
    ("Zithromax 500mg",            "Azithromycin",        "Pfizer Pakistan",     "Tablet",     "3s strip",   220,  320),
    ("Cefspan 200mg",              "Cefixime",            "Getz Pharma",         "Capsule",    "10s strip",  260,  380),
    ("Risek 20mg",                 "Omeprazole",          "Getz Pharma",         "Capsule",    "14s strip",  180,  260),
    ("Nexum 40mg",                 "Esomeprazole",        "Getz Pharma",         "Tablet",     "14s strip",  260,  360),
    ("Tecta 40mg",                 "Pantoprazole",        "Sante Pakistan",      "Tablet",     "14s strip",  220,  320),
    ("Motilium 10mg",              "Domperidone",         "Janssen Pakistan",    "Tablet",     "10s strip",   60,   95),
    ("Glucophage 500mg",           "Metformin",           "Merck Pakistan",      "Tablet",     "20s strip",   80,  120),
    ("Amaryl 2mg",                 "Glimepiride",         "Sanofi Pakistan",     "Tablet",     "10s strip",  140,  200),
    ("Norvasc 5mg",                "Amlodipine",          "Pfizer Pakistan",     "Tablet",     "10s strip",   90,  140),
    ("Cozaar 50mg",                "Losartan Potassium",  "Organon Pakistan",    "Tablet",     "10s strip",  130,  190),
    ("Micardis 40mg",              "Telmisartan",         "Boehringer Ingelheim","Tablet",     "14s strip",  240,  340),
    ("Lipitor 20mg",               "Atorvastatin",        "Pfizer Pakistan",     "Tablet",     "10s strip",  180,  260),
    ("Crestor 10mg",               "Rosuvastatin",        "AstraZeneca Pakistan","Tablet",     "10s strip",  220,  310),
    ("Zyrtec 10mg",                "Cetirizine",          "GSK Pakistan",        "Tablet",     "10s strip",   40,   65),
    ("Xyzal 5mg",                  "Levocetirizine",      "Sanofi Pakistan",     "Tablet",     "10s strip",   60,   95),
    ("Provair 10mg",               "Montelukast",         "Getz Pharma",         "Tablet",     "10s strip",  180,  260),
    ("Telfast 120mg",              "Fexofenadine",        "Sanofi Pakistan",     "Tablet",     "10s strip",  130,  190),
    ("Ventolin Inhaler",           "Salbutamol",          "GSK Pakistan",        "Other",      "1 inhaler",  280,  400),
    ("Mucolyt Syrup 100ml",        "Ambroxol",            "Highnoon Labs",       "Syrup",      "100ml bottle",95,  150),
    ("Dexonto Syrup 60ml",         "Dextromethorphan",    "Barrett Hodgson",     "Syrup",      "60ml bottle", 75,  120),
    ("WHO-ORS Sachet",             "Oral Rehydration Salts","Getz Pharma",       "Other",      "1 sachet",    12,   20),
    ("Surbex-Z Tablet",            "Multivitamin",        "Abbott Pakistan",     "Tablet",     "10s strip",   80,  130),
    ("Vidalta D3 60000IU",         "Vitamin D3",          "Sante Pakistan",      "Capsule",    "4s strip",   150,  220),
    ("Calcimax Tablet",            "Calcium + Vitamin D3","Brookes Pharma",      "Tablet",     "10s strip",   70,  110),
    ("Ferrograd Tablet",           "Ferrous Sulphate",    "Abbott Pakistan",     "Tablet",     "10s strip",   65,  100),
    ("Iberet Folic Syrup 120ml",   "Iron + Folic Acid",   "Abbott Pakistan",     "Syrup",      "120ml bottle",110,  170),
    ("Panadol Baby Drops 15ml",    "Paracetamol",         "GSK Pakistan",        "Drops",      "15ml bottle", 90,  140),
    ("Nano Drops 15ml",            "Sodium Chloride",     "Barrett Hodgson",     "Drops",      "15ml bottle", 55,   90),
    ("Ciplox Eye Drops",           "Ciprofloxacin",       "Cipla Pakistan",      "Drops",      "5ml bottle",  70,  115),
    ("Refresh Tears Eye Drops",    "Carboxymethylcellulose","Allergan Pakistan","Drops",      "10ml bottle",130,  195),
    ("Waxsol Ear Drops",           "Docusate Sodium",     "Barrett Hodgson",     "Drops",      "10ml bottle", 95,  150),
    ("Betnovate Cream 20g",        "Betamethasone",       "GSK Pakistan",        "Cream",      "20g tube",    80,  130),
    ("Candid Cream 20g",           "Clotrimazole",        "Glenmark Pakistan",   "Cream",      "20g tube",    90,  140),
    ("Fucidin Cream 15g",          "Fusidic Acid",        "LEO Pharma",          "Cream",      "15g tube",   180,  260),
    ("Moisturex Cream 100g",       "Urea Cream",          "Genome Pharmaceuticals","Cream",    "100g jar",   190,  280),
    ("Neosporin Ointment 15g",     "Neomycin + Bacitracin","GSK Pakistan",       "Cream",      "15g tube",   110,  170),
    ("Voltral Emulgel 30g",        "Diclofenac Gel",      "Novartis Pakistan",   "Cream",      "30g tube",   170,  250),
    ("Augmentin 1.2g Injection",   "Amoxicillin/Clavulanate","GSK Pakistan",     "Injection",  "1 vial",     260,  380),
    ("Voren 75mg Injection",       "Diclofenac Sodium",   "Novartis Pakistan",   "Injection",  "1 ampoule",   45,   75),
    ("Buscopan Injection",         "Hyoscine Butylbromide","Sanofi Pakistan",    "Injection",  "1 ampoule",   55,   90),
    ("Insulin Novomix 30",         "Insulin Aspart",      "Novo Nordisk",        "Injection",  "1 pen",      900, 1250),
    ("Tetanus Toxoid Injection",   "Tetanus Toxoid",      "National Institute of Health","Injection","1 vial",  40,   70),
    ("Avil 25mg Injection",        "Pheniramine Maleate", "Sanofi Pakistan",     "Injection",  "1 ampoule",   30,   55),
    ("Flagyl 400mg",               "Metronidazole",       "Sanofi Pakistan",     "Tablet",     "10s strip",   45,   75),
    ("Ciproxin 500mg",             "Ciprofloxacin",       "Bayer Pakistan",      "Tablet",     "10s strip",  110,  170),
    ("Levoflox 500mg",             "Levofloxacin",        "Getz Pharma",         "Tablet",     "5s strip",   180,  260),
    ("Klaricid 500mg",             "Clarithromycin",      "Abbott Pakistan",     "Tablet",     "7s strip",   260,  380),
    ("Panadol Extra",              "Paracetamol + Caffeine","GSK Pakistan",      "Tablet",     "10s strip",   40,   65),
    ("Disprin",                    "Aspirin",             "Reckitt Benckiser",   "Tablet",     "10s strip",   25,   40),
    ("Ponstan 500mg",              "Mefenamic Acid",      "Pfizer Pakistan",     "Capsule",    "10s strip",   55,   90),
    ("Arinac Forte",               "Paracetamol + Pseudoephedrine + CPM", "Abbott Pakistan","Tablet","10s strip", 45,   70),
    ("Neurobion Forte",            "Vitamin B Complex",   "Merck Pakistan",      "Tablet",     "10s strip",   50,   80),
    ("Surbex T",                   "Vitamin B Complex + Vitamin C","Abbott Pakistan","Tablet","10s strip",   70,  115),
    ("Folic Acid 5mg",             "Folic Acid",          "Barrett Hodgson",     "Tablet",     "10s strip",   25,   40),
    ("Osteocare Tablet",           "Calcium + Vitamin D3 + Zinc","Vitabiotics", "Tablet",     "30s bottle",  350,  520),
    ("Duphaston 10mg",             "Dydrogesterone",      "Abbott Pakistan",     "Tablet",     "10s strip",  260,  380),
    ("Yasmin Tablet",              "Drospirenone + Ethinylestradiol","Bayer Pakistan","Tablet","21s strip",  380,  550),
    ("Glucobay 50mg",              "Acarbose",            "Bayer Pakistan",      "Tablet",     "10s strip",  120,  180),
    ("Diamicron MR 60mg",          "Gliclazide",          "Servier Pakistan",    "Tablet",     "10s strip",  180,  260),
    ("Concor 5mg",                 "Bisoprolol",          "Merck Pakistan",      "Tablet",     "10s strip",  140,  200),
    ("Inderal 10mg",               "Propranolol",         "AGP Pakistan",        "Tablet",     "10s strip",   35,   55),
    ("Lasix 40mg",                 "Furosemide",          "Sanofi Pakistan",     "Tablet",     "10s strip",   25,   40),
    ("Aldactone 25mg",             "Spironolactone",      "Pfizer Pakistan",     "Tablet",     "10s strip",   60,   95),
    ("Rivotril 0.5mg",             "Clonazepam",          "Roche Pakistan",      "Tablet",     "10s strip",   45,   75),
    ("Xanax 0.5mg",                "Alprazolam",          "Pfizer Pakistan",     "Tablet",     "10s strip",   30,   50),
    ("Zoloft 50mg",                "Sertraline",          "Pfizer Pakistan",     "Tablet",     "14s strip",  220,  320),
    ("Cipralex 10mg",              "Escitalopram",        "H.Lundbeck Pakistan", "Tablet",     "14s strip",  260,  380),
    ("Epival 200mg",               "Sodium Valproate",    "Sanofi Pakistan",     "Tablet",     "10s strip",  120,  180),
    ("Tegral 200mg",               "Carbamazepine",       "Novartis Pakistan",   "Tablet",     "10s strip",   65,  100),
    ("Panadol CF",                 "Paracetamol + Phenylephrine + CPM","GSK Pakistan","Tablet","10s strip",   45,   70),
    ("Rigix 10mg",                 "Domperidone + Cinnarizine","Sante Pakistan", "Tablet",     "10s strip",   90,  140),
    ("Gaviscon Syrup 150ml",       "Alginic Acid",        "Reckitt Benckiser",   "Syrup",      "150ml bottle",180,  260),
    ("Digene Gel 170ml",           "Antacid",             "Abbott Pakistan",     "Syrup",      "170ml bottle",120,  180),
    ("Eno Sachet",                 "Sodium Bicarbonate",  "GSK Pakistan",        "Other",      "1 sachet",    15,   25),
    ("Ostocalcium Syrup 200ml",    "Calcium Gluconate",   "GSK Pakistan",        "Syrup",      "200ml bottle",130,  190),
    ("Zinc Sulphate Syrup 60ml",   "Zinc Sulphate",       "Brookes Pharma",      "Syrup",      "60ml bottle", 55,   90),
    ("Polybion Syrup 200ml",       "Vitamin B Complex",   "Merck Pakistan",      "Syrup",      "200ml bottle",130,  190),
    ("Panadol Suppository 125mg",  "Paracetamol",         "GSK Pakistan",        "Other",      "5s pack",     60,   95),
    ("Combiflam Tablet",           "Ibuprofen + Paracetamol","Sanofi Pakistan",  "Tablet",     "10s strip",   50,   80),
    ("Naprosyn 500mg",             "Naproxen",            "Atco Laboratories",   "Tablet",     "10s strip",   55,   85),
    ("Tramal 50mg",                "Tramadol",            "Searle Pakistan",     "Capsule",    "10s strip",   85,  130),
    ("Rablet 20mg",                "Rabeprazole",         "Getz Pharma",         "Tablet",     "14s strip",  200,  290),
    ("Buscopan Tablet",            "Hyoscine Butylbromide","Sanofi Pakistan",    "Tablet",     "20s strip",   90,  140),
    ("Colomax Tablet",             "Mebeverine",          "Abbott Pakistan",     "Tablet",     "10s strip",  100,  150),
    ("Eldoper Capsule",            "Loperamide",          "Searle Pakistan",     "Capsule",    "6s strip",    35,   55),
    ("Dulcolax 5mg",               "Bisacodyl",           "Sanofi Pakistan",     "Tablet",     "10s strip",   35,   55),
    ("Duphalac Syrup 200ml",       "Lactulose",           "Abbott Pakistan",     "Syrup",      "200ml bottle",240,  350),
    ("Risek IV 40mg Injection",    "Omeprazole",          "Getz Pharma",         "Injection",  "1 vial",     220,  320),
    ("Dexamethasone Injection",    "Dexamethasone",       "Barrett Hodgson",     "Injection",  "1 ampoule",   35,   55),
    ("Ceftriaxone 1g Injection",   "Ceftriaxone",         "Getz Pharma",         "Injection",  "1 vial",     140,  200),
    ("Lasix Injection",            "Furosemide",          "Sanofi Pakistan",     "Injection",  "1 ampoule",   30,   50),
    ("Metronidazole IV Infusion",  "Metronidazole",       "Barrett Hodgson",     "Injection",  "100ml bag",   85,  130),
    ("Normal Saline IV 500ml",     "Sodium Chloride 0.9%","Otsuka Pakistan",     "Injection",  "500ml bag",   90,  140),
    ("Dextrose 5% IV 500ml",       "Dextrose",            "Otsuka Pakistan",     "Injection",  "500ml bag",   95,  145),
    ("Ringer's Lactate IV 500ml",  "Compound Sodium Lactate","Otsuka Pakistan",  "Injection",  "500ml bag",   95,  145),
    ("Betadine Solution 100ml",    "Povidone Iodine",     "Mundipharma Pakistan","Other",      "100ml bottle",120,  180),
    ("Surgical Spirit 100ml",      "Isopropyl Alcohol",   "Global Pharmaceuticals","Other",    "100ml bottle", 60,   95),
]

assert len(MEDICINES) >= 100, f"Only {len(MEDICINES)} medicines — need at least 100."

# Non-medical, non-batch-tracked pharmacy-adjacent items — the generic
# store/personal-care side of the same demo business. Kept in this file
# alongside MEDICINES since both feed the same stage.
#
# category_hint values are existing DEFAULT_BUSINESS_CATEGORIES names
# (salpurflask/services/category_catalog.py) — "Medical Store", "Personal
# Care", "Baby Care", "Grocery" — reused deliberately rather than inventing
# a new category, per the no-unnecessary-duplication instruction. Surgical/
# clinical supplies go under "Medical Store" alongside the medicines.
#
# (name, category_hint, unit, purchase_price, sale_price)
NON_MEDICAL_ITEMS = [
    ("Huggies Baby Diapers Small (20s)",       "Baby Care",     "Pack", 480, 650),
    ("Huggies Baby Diapers Medium (18s)",      "Baby Care",     "Pack", 520, 700),
    ("Pampers Baby Wipes 80s",                 "Baby Care",     "Pack", 250, 340),
    ("Johnson's Baby Powder 200g",             "Baby Care",     "Pcs",  280, 380),
    ("Johnson's Baby Oil 200ml",                "Baby Care",     "Pcs",  320, 430),
    ("Colgate Toothpaste 100g",                "Personal Care", "Pcs",  140, 190),
    ("Sensodyne Toothpaste 75g",               "Personal Care", "Pcs",  260, 350),
    ("Oral-B Toothbrush",                      "Personal Care", "Pcs",   90, 140),
    ("Head & Shoulders Shampoo 185ml",         "Personal Care", "Pcs",  360, 480),
    ("Sunsilk Shampoo 375ml",                  "Personal Care", "Pcs",  420, 560),
    ("Lifebuoy Soap 115g",                     "Personal Care", "Pcs",   55,   80),
    ("Dettol Soap 105g",                       "Personal Care", "Pcs",   70,  100),
    ("Dettol Antiseptic Liquid 125ml",         "Personal Care", "Pcs",  180, 250),
    ("Safeguard Hand Wash 225ml",              "Personal Care", "Pcs",  220, 300),
    ("Facial Tissue Box 200 Pulls",            "Personal Care", "Box",  120, 170),
    ("Toilet Roll Pack of 4",                  "Personal Care", "Pack", 180, 250),
    ("Always Sanitary Pads (10s)",             "Personal Care", "Pack", 130, 190),
    ("Whisper Sanitary Pads (8s)",             "Personal Care", "Pack", 150, 210),
    ("Surgical Face Mask (50s Box)",           "Medical Store", "Box",  350, 500),
    ("Nitrile Examination Gloves (100s Box)",  "Medical Store", "Box",  650, 900),
    ("Cotton Bandage Roll 4inch",              "Medical Store", "Pcs",   35,   55),
    ("Elastic Crepe Bandage 6inch",            "Medical Store", "Pcs",   90,  135),
    ("Adhesive Plaster Roll 1inch",            "Medical Store", "Pcs",   45,   70),
    ("Sterile Gauze Swabs (10s Pack)",         "Medical Store", "Pack",  60,   95),
    ("Digital Thermometer",                    "Medical Store", "Pcs",  280, 400),
    ("Blood Pressure Monitor (Digital)",       "Medical Store", "Pcs", 2800,3800),
    ("Glucometer Device",                      "Medical Store", "Pcs", 1800,2500),
    ("Glucometer Test Strips (25s)",           "Medical Store", "Pack", 900,1250),
    ("Insulin Syringe 1ml (10s Pack)",         "Medical Store", "Pack", 120, 170),
    ("Disposable Syringe 5ml (10s Pack)",      "Medical Store", "Pack",  90,  130),
    ("Cotton Wool Roll 100g",                  "Medical Store", "Pcs",   70,  110),
    ("First Aid Kit Box",                      "Medical Store", "Box",  650, 900),
    ("Basmati Rice 5kg",                       "Grocery",       "Pack", 950,1200),
    ("Cooking Oil 5L",                         "Grocery",       "Pcs", 2200,2600),
    ("Sugar 2kg",                              "Grocery",       "Pack", 280, 340),
    ("Tea Pack 400g",                          "Grocery",       "Pack", 480, 580),
    ("Salt 800g",                              "Grocery",       "Pack",  35,   50),
    ("Milk Pack 1L (UHT)",                     "Grocery",       "Pcs",  190, 230),
    ("Biscuits Family Pack",                   "Grocery",       "Pack", 140, 190),
    ("Mineral Water 1.5L",                     "Grocery",       "Pcs",   60,   80),
    ("Energy Drink Can 250ml",                 "Grocery",       "Pcs",  110, 150),
]

assert len(NON_MEDICAL_ITEMS) >= 30, f"Only {len(NON_MEDICAL_ITEMS)} non-medical items."
