"""Seed data for the DVAGO demo dataset: real brands, real areas, real prices.

SYNTHETIC DATASET, REAL REFERENCE POINTS. The customers, orders, complaints and
stock levels generated from this file are invented. The *brands*, *manufacturers*
and *areas* are real, because a demo where the caller asks for "Panadol" and the
agent has never heard of it tells you nothing about whether the agent works.

Prices are mid-2026 Pakistani retail in PKR, rounded to the nearest sensible
shelf price. They move with DRAP notifications and are indicative, not a
quotation.

Kept apart from the generator so the catalogue can be reviewed by someone who
knows Pakistani pharmacy retail without reading any Python.
"""

from __future__ import annotations

# ── Store network ────────────────────────────────────────────────────────
# (city, area, branch_name, landmark, is_24_7, cold_chain, express, lat, lng)
#
# Cities are the eight where DVAGO actually operates retail stores. Everywhere
# else is courier-only, which is a distinction the agent has to get right: it
# is the difference between "one hour" and "two to three days".
BRANCHES: list[tuple[str, str, str, str, int, int, int, float, float]] = [
    # Karachi — the densest network, and where most demo calls will come from
    ("Karachi", "Bahadurabad", "DVAGO Bahadurabad", "Near Dhoraji Colony roundabout", 1, 1, 1, 24.8760, 67.0700),  # noqa: E501
    ("Karachi", "Clifton", "DVAGO Clifton Block 5", "Opposite Clifton Centre", 1, 1, 1, 24.8138, 67.0300),  # noqa: E501
    ("Karachi", "DHA Phase 5", "DVAGO DHA Phase 5", "Khayaban-e-Shahbaz, near Nueplex", 1, 1, 1, 24.8000, 67.0600),  # noqa: E501
    ("Karachi", "DHA Phase 6", "DVAGO DHA Phase 6", "Khayaban-e-Bukhari commercial", 0, 1, 1, 24.7980, 67.0450),  # noqa: E501
    ("Karachi", "Gulshan-e-Iqbal", "DVAGO Gulshan Block 13-B", "Main University Road, opposite Bait-ul-Mukarram", 1, 1, 1, 24.9200, 67.0900),  # noqa: E501
    ("Karachi", "Gulistan-e-Johar", "DVAGO Gulistan-e-Johar", "Block 15, near Perfume Chowk", 0, 1, 1, 24.9250, 67.1300),  # noqa: E501
    ("Karachi", "North Nazimabad", "DVAGO North Nazimabad", "Block H, near Five Star Chowrangi", 0, 1, 1, 24.9400, 67.0400),  # noqa: E501
    ("Karachi", "F.B. Area", "DVAGO F.B. Area", "Block 15, Dastagir", 0, 0, 1, 24.9350, 67.0700),  # noqa: E501
    ("Karachi", "PECHS", "DVAGO PECHS Block 6", "Off Shahra-e-Faisal, near Nursery", 0, 1, 1, 24.8600, 67.0650),  # noqa: E501
    ("Karachi", "Tariq Road", "DVAGO Tariq Road", "Near Dolmen Mall Tariq Road", 0, 0, 1, 24.8700, 67.0620),  # noqa: E501
    ("Karachi", "Nazimabad", "DVAGO Nazimabad No. 3", "Near Nazimabad Petrol Pump", 0, 0, 1, 24.9100, 67.0300),  # noqa: E501
    ("Karachi", "Korangi", "DVAGO Korangi Sector 33-A", "Near Korangi Crossing", 0, 0, 0, 24.8300, 67.1400),  # noqa: E501
    ("Karachi", "Malir", "DVAGO Malir Cantt", "Near Malir Halt", 0, 0, 0, 24.8900, 67.2000),
    ("Karachi", "Saddar", "DVAGO Saddar", "Near Empress Market", 0, 0, 1, 24.8600, 67.0250),
    ("Karachi", "Shahra-e-Faisal", "DVAGO Shahra-e-Faisal", "Near Awami Markaz", 1, 1, 1, 24.8650, 67.0800),  # noqa: E501
    ("Karachi", "Gulshan-e-Hadeed", "DVAGO Gulshan-e-Hadeed", "Phase 2, near main market", 0, 0, 0, 24.8700, 67.3400),  # noqa: E501
    # Lahore
    ("Lahore", "Gulberg", "DVAGO Gulberg III", "Main Boulevard, near Liberty", 1, 1, 1, 31.5100, 74.3450),  # noqa: E501
    ("Lahore", "DHA Phase 4", "DVAGO DHA Phase 4", "Sector CCA, near Y-Block market", 1, 1, 1, 31.4700, 74.4100),  # noqa: E501
    ("Lahore", "Johar Town", "DVAGO Johar Town", "Near Emporium Mall", 0, 1, 1, 31.4650, 74.2700),
    ("Lahore", "Model Town", "DVAGO Model Town", "Near Model Town Link Road", 0, 1, 1, 31.4800, 74.3200),  # noqa: E501
    ("Lahore", "Bahria Town", "DVAGO Bahria Town Lahore", "Sector C, near Grand Jamia Masjid", 0, 0, 1, 31.3650, 74.1800),  # noqa: E501
    ("Lahore", "Faisal Town", "DVAGO Faisal Town", "Akbar Chowk", 0, 0, 1, 31.4800, 74.3000),
    ("Lahore", "Wapda Town", "DVAGO Wapda Town", "Near Wapda Town roundabout", 0, 0, 1, 31.4300, 74.2600),  # noqa: E501
    ("Lahore", "Kahna", "DVAGO Kahna", "Main Ferozepur Road, Kahna Nau", 0, 0, 0, 31.3700, 74.4000),
    ("Lahore", "Quaid-e-Azam Industrial Estate", "DVAGO Kot Lakhpat", "Hamdard Chowk, Block M", 0, 0, 0, 31.4400, 74.3300),  # noqa: E501
    # Hyderabad
    ("Hyderabad", "Latifabad", "DVAGO Latifabad Unit 7", "Near Chandni Chowk", 0, 1, 1, 25.3700, 68.3400),  # noqa: E501
    ("Hyderabad", "Qasimabad", "DVAGO Qasimabad", "Near Wadhu Wah Road", 0, 0, 1, 25.4000, 68.3200),
    # Multan
    ("Multan", "Gulgasht Colony", "DVAGO Gulgasht", "Near Nishtar Hospital", 0, 1, 1, 30.2200, 71.4700),  # noqa: E501
    ("Multan", "Bosan Road", "DVAGO Bosan Road", "Near Chungi No. 9", 0, 0, 1, 30.2100, 71.4400),
    # Bahawalpur
    ("Bahawalpur", "Model Town A", "DVAGO Bahawalpur", "Near Bahawal Victoria Hospital", 0, 0, 0, 29.3950, 71.6830),  # noqa: E501
    # Kasur
    ("Kasur", "Kacheri Road", "DVAGO Kasur", "Near District Courts", 0, 0, 0, 31.1150, 74.4500),
    # Sahiwal
    ("Sahiwal", "Farid Town", "DVAGO Sahiwal", "Near Farid Town Chowk", 0, 0, 0, 30.6700, 73.1100),
    # Burewala
    ("Burewala", "College Road", "DVAGO Burewala", "Near Government College", 0, 0, 0, 30.1600, 72.6500),  # noqa: E501
]

# Courier-only cities: the agent must not promise one-hour delivery here.
COURIER_ONLY_CITIES = [
    "Islamabad", "Rawalpindi", "Faisalabad", "Peshawar", "Quetta", "Sialkot",
    "Gujranwala", "Sukkur", "Larkana", "Abbottabad", "Sargodha", "Rahim Yar Khan",
]

# ── Catalogue ────────────────────────────────────────────────────────────
# (brand, generic, strength, form, pack, units, manufacturer, category,
#  subcategory, therapeutic_class, price_pkr, rx, controlled, cold_chain,
#  search_terms)
#
# search_terms carries the misspellings and Urdu spellings a caller actually
# produces. Deepgram will hand us "panadole" or "پیناڈول" and the lookup has to
# survive it — matching only on the exact brand string is the single most
# common way a pharmacy bot fails on the phone.
PRODUCTS: list[tuple[str, str, str, str, str, int, str, str, str, str, float, int, int, int, str]] = [  # noqa: E501
    # ── Analgesics and antipyretics ──────────────────────────────────────
    ("Panadol", "Paracetamol", "500mg", "tablet", "10 tablets", 10, "GSK Pakistan", "Medicines", "Pain Relief", "analgesic", 65, 0, 0, 0, "panadole|panadol tablet|paracetamol|بخار کی گولی|پیناڈول"),  # noqa: E501
    ("Panadol Extra", "Paracetamol + Caffeine", "500mg+65mg", "tablet", "10 tablets", 10, "GSK Pakistan", "Medicines", "Pain Relief", "analgesic", 95, 0, 0, 0, "panadol extra|extra panadol|پیناڈول ایکسٹرا"),  # noqa: E501
    ("Panadol CF", "Paracetamol + Pseudoephedrine + Chlorpheniramine", "500mg", "tablet", "10 tablets", 10, "GSK Pakistan", "Medicines", "Cough & Cold", "analgesic", 110, 0, 0, 0, "panadol cf|cold flu|زکام"),  # noqa: E501
    ("Calpol", "Paracetamol", "120mg/5ml", "syrup", "60ml bottle", 1, "GSK Pakistan", "Medicines", "Pain Relief", "analgesic", 145, 0, 0, 0, "calpol|kalpol|bachon ka bukhar|کیلپول"),  # noqa: E501
    ("Brufen", "Ibuprofen", "400mg", "tablet", "10 tablets", 10, "Abbott Pakistan", "Medicines", "Pain Relief", "nsaid", 130, 0, 0, 0, "brufen|broufen|ibuprofen|بروفن"),  # noqa: E501
    ("Brufen Syrup", "Ibuprofen", "100mg/5ml", "syrup", "120ml bottle", 1, "Abbott Pakistan", "Medicines", "Pain Relief", "nsaid", 235, 0, 0, 0, "brufen syrup|bruffen"),  # noqa: E501
    ("Disprin", "Aspirin", "300mg", "tablet", "10 tablets", 10, "Reckitt Benckiser Pakistan", "Medicines", "Pain Relief", "nsaid", 55, 0, 0, 0, "disprin|dispirin|ڈسپرین"),  # noqa: E501
    ("Synflex", "Naproxen Sodium", "550mg", "tablet", "10 tablets", 10, "The Searle Company", "Medicines", "Pain Relief", "nsaid", 320, 0, 0, 0, "synflex|sinflex|naproxen"),  # noqa: E501
    ("Voren", "Diclofenac Sodium", "50mg", "tablet", "20 tablets", 20, "Bosch Pharmaceuticals", "Medicines", "Pain Relief", "nsaid", 180, 0, 0, 0, "voren|voran|diclofenac"),  # noqa: E501
    ("Nims", "Nimesulide", "100mg", "tablet", "10 tablets", 10, "Hilton Pharma", "Medicines", "Pain Relief", "nsaid", 165, 0, 0, 0, "nims|nimesulide"),  # noqa: E501
    ("Ponstan", "Mefenamic Acid", "500mg", "tablet", "10 tablets", 10, "Pfizer Pakistan", "Medicines", "Pain Relief", "nsaid", 240, 0, 0, 0, "ponstan|ponstan forte|mefenamic"),  # noqa: E501
    ("Tramal", "Tramadol", "50mg", "capsule", "10 capsules", 10, "Sami Pharmaceuticals", "Medicines", "Pain Relief", "opioid analgesic", 390, 1, 1, 0, "tramal|tramadol|ٹرامل"),  # noqa: E501

    # ── Antibiotics ──────────────────────────────────────────────────────
    ("Augmentin", "Amoxicillin + Clavulanic Acid", "625mg", "tablet", "6 tablets", 6, "GSK Pakistan", "Medicines", "Antibiotics", "antibiotic", 780, 1, 0, 0, "augmentin|agmentin|augmenten|اموکسیلن|آگمنٹن"),  # noqa: E501
    ("Augmentin Syrup", "Amoxicillin + Clavulanic Acid", "228mg/5ml", "suspension", "35ml bottle", 1, "GSK Pakistan", "Medicines", "Antibiotics", "antibiotic", 560, 1, 0, 0, "augmentin syrup|augmentin bachon"),  # noqa: E501
    ("Amoxil", "Amoxicillin", "500mg", "capsule", "12 capsules", 12, "GSK Pakistan", "Medicines", "Antibiotics", "antibiotic", 390, 1, 0, 0, "amoxil|amoxicillin|اموکسل"),  # noqa: E501
    ("Velosef", "Cephradine", "500mg", "capsule", "12 capsules", 12, "GSK Pakistan", "Medicines", "Antibiotics", "antibiotic", 480, 1, 0, 0, "velosef|velocef|cephradine"),  # noqa: E501
    ("Cefspan", "Cefixime", "400mg", "capsule", "5 capsules", 5, "Barrett Hodgson Pakistan", "Medicines", "Antibiotics", "antibiotic", 720, 1, 0, 0, "cefspan|sefspan|cefixime"),  # noqa: E501
    ("Zithromax", "Azithromycin", "500mg", "tablet", "3 tablets", 3, "Pfizer Pakistan", "Medicines", "Antibiotics", "antibiotic", 690, 1, 0, 0, "zithromax|azithromycin|azomax"),  # noqa: E501
    ("Klaricid", "Clarithromycin", "500mg", "tablet", "10 tablets", 10, "Abbott Pakistan", "Medicines", "Antibiotics", "antibiotic", 1450, 1, 0, 0, "klaricid|claricid|clarithromycin"),  # noqa: E501
    ("Ciproxin", "Ciprofloxacin", "500mg", "tablet", "10 tablets", 10, "Bayer Pakistan", "Medicines", "Antibiotics", "antibiotic", 620, 1, 0, 0, "ciproxin|cipro|ciprofloxacin"),  # noqa: E501
    ("Flagyl", "Metronidazole", "400mg", "tablet", "20 tablets", 20, "Sanofi-Aventis Pakistan", "Medicines", "Antibiotics", "antibiotic", 210, 1, 0, 0, "flagyl|flagil|metronidazole|فلیجل"),  # noqa: E501
    ("Moxiflox", "Moxifloxacin", "400mg", "tablet", "5 tablets", 5, "Getz Pharma", "Medicines", "Antibiotics", "antibiotic", 880, 1, 0, 0, "moxiflox|moxifloxacin"),  # noqa: E501

    # ── Gastro ───────────────────────────────────────────────────────────
    ("Risek", "Omeprazole", "20mg", "capsule", "14 capsules", 14, "Getz Pharma", "Medicines", "Gastro", "ppi", 470, 1, 0, 0, "risek|rissek|omeprazole|معدے کی گولی|رائزک"),  # noqa: E501
    ("Nexum", "Esomeprazole", "40mg", "capsule", "14 capsules", 14, "Getz Pharma", "Medicines", "Gastro", "ppi", 720, 1, 0, 0, "nexum|nexium|esomeprazole"),  # noqa: E501
    ("Zantac", "Ranitidine", "150mg", "tablet", "20 tablets", 20, "GSK Pakistan", "Medicines", "Gastro", "h2 blocker", 195, 0, 0, 0, "zantac|zentac|ranitidine"),  # noqa: E501
    ("Motilium", "Domperidone", "10mg", "tablet", "30 tablets", 30, "Johnson & Johnson Pakistan", "Medicines", "Gastro", "prokinetic", 340, 0, 0, 0, "motilium|motillium|domperidone"),  # noqa: E501
    ("Gravinate", "Dimenhydrinate", "50mg", "tablet", "10 tablets", 10, "The Searle Company", "Medicines", "Gastro", "antiemetic", 130, 0, 0, 0, "gravinate|graviniate|قے کی گولی"),  # noqa: E501
    ("Imodium", "Loperamide", "2mg", "capsule", "6 capsules", 6, "Johnson & Johnson Pakistan", "Medicines", "Gastro", "antidiarrhoeal", 175, 0, 0, 0, "imodium|imodum|loperamide|دست"),  # noqa: E501
    ("Entamizole", "Diloxanide + Metronidazole", "-", "tablet", "20 tablets", 20, "Abbott Pakistan", "Medicines", "Gastro", "antiamoebic", 260, 0, 0, 0, "entamizole|antamizole"),  # noqa: E501
    ("Peditral", "Oral Rehydration Salts", "-", "sachet", "6 sachets", 6, "The Searle Company", "Medicines", "Gastro", "ors", 150, 0, 0, 0, "peditral|ors|nimkol|او آر ایس"),  # noqa: E501
    ("Buscopan", "Hyoscine Butylbromide", "10mg", "tablet", "20 tablets", 20, "Sanofi-Aventis Pakistan", "Medicines", "Gastro", "antispasmodic", 290, 0, 0, 0, "buscopan|buskopan|pait dard"),  # noqa: E501

    # ── Diabetes ─────────────────────────────────────────────────────────
    ("Glucophage", "Metformin", "500mg", "tablet", "20 tablets", 20, "Merck Pakistan", "Medicines", "Diabetes", "antidiabetic", 145, 1, 0, 0, "glucophage|glukophage|metformin|شوگر کی گولی"),  # noqa: E501
    ("Glucophage XR", "Metformin", "1000mg", "tablet", "30 tablets", 30, "Merck Pakistan", "Medicines", "Diabetes", "antidiabetic", 460, 1, 0, 0, "glucophage xr|metformin xr"),  # noqa: E501
    ("Amaryl", "Glimepiride", "2mg", "tablet", "10 tablets", 10, "Sanofi-Aventis Pakistan", "Medicines", "Diabetes", "antidiabetic", 385, 1, 0, 0, "amaryl|amryl|glimepiride"),  # noqa: E501
    ("Januvia", "Sitagliptin", "50mg", "tablet", "14 tablets", 14, "MSD Pakistan", "Medicines", "Diabetes", "antidiabetic", 2350, 1, 0, 0, "januvia|janumet|sitagliptin"),  # noqa: E501
    ("Lantus SoloStar", "Insulin Glargine", "100 IU/ml", "injection", "1 pen (3ml)", 1, "Sanofi-Aventis Pakistan", "Medicines", "Diabetes", "insulin", 4350, 1, 0, 1, "lantus|solostar|insulin glargine|انسولین"),  # noqa: E501
    ("NovoMix 30 FlexPen", "Insulin Aspart", "100 IU/ml", "injection", "1 pen (3ml)", 1, "Novo Nordisk Pakistan", "Medicines", "Diabetes", "insulin", 2480, 1, 0, 1, "novomix|novo mix|flexpen|insulin"),  # noqa: E501
    ("Humulin N", "Insulin Isophane", "100 IU/ml", "injection", "1 vial (10ml)", 1, "Eli Lilly Pakistan", "Medicines", "Diabetes", "insulin", 1980, 1, 0, 1, "humulin|humulin n|insulin vial"),  # noqa: E501
    ("Accu-Chek Active Strips", "Glucose Test Strips", "-", "strips", "50 strips", 50, "Roche Pakistan", "Devices & Support", "Diabetes Care", "diagnostic", 3150, 0, 0, 0, "accuchek|accu chek|sugar strip|test strip|شوگر سٹرپ"),  # noqa: E501
    ("BD Insulin Syringe", "Insulin Syringe 1ml", "31G", "syringe", "10 syringes", 10, "Becton Dickinson", "Devices & Support", "Diabetes Care", "device", 420, 0, 0, 0, "insulin syringe|bd syringe|سرنج"),  # noqa: E501

    # ── Cardiovascular ───────────────────────────────────────────────────
    ("Lipitor", "Atorvastatin", "10mg", "tablet", "10 tablets", 10, "Pfizer Pakistan", "Medicines", "Cardiovascular", "statin", 520, 1, 0, 0, "lipitor|lipiter|atorvastatin|کولیسٹرول"),  # noqa: E501
    ("Concor", "Bisoprolol", "5mg", "tablet", "14 tablets", 14, "Merck Pakistan", "Medicines", "Cardiovascular", "beta blocker", 480, 1, 0, 0, "concor|konkor|bisoprolol"),  # noqa: E501
    ("Tenormin", "Atenolol", "50mg", "tablet", "14 tablets", 14, "AstraZeneca Pakistan", "Medicines", "Cardiovascular", "beta blocker", 265, 1, 0, 0, "tenormin|atenolol|بلڈ پریشر"),  # noqa: E501
    ("Norvasc", "Amlodipine", "5mg", "tablet", "14 tablets", 14, "Pfizer Pakistan", "Medicines", "Cardiovascular", "calcium blocker", 340, 1, 0, 0, "norvasc|norvask|amlodipine"),  # noqa: E501
    ("Exforge", "Amlodipine + Valsartan", "5mg+80mg", "tablet", "14 tablets", 14, "Novartis Pakistan", "Medicines", "Cardiovascular", "antihypertensive", 1120, 1, 0, 0, "exforge|exforg"),  # noqa: E501
    ("Cardiprin", "Aspirin", "100mg", "tablet", "30 tablets", 30, "Reckitt Benckiser Pakistan", "Medicines", "Cardiovascular", "antiplatelet", 180, 0, 0, 0, "cardiprin|aspirin 100|loprin"),  # noqa: E501
    ("Plavix", "Clopidogrel", "75mg", "tablet", "14 tablets", 14, "Sanofi-Aventis Pakistan", "Medicines", "Cardiovascular", "antiplatelet", 1380, 1, 0, 0, "plavix|clopidogrel|osvix"),  # noqa: E501
    ("Lasix", "Furosemide", "40mg", "tablet", "10 tablets", 10, "Sanofi-Aventis Pakistan", "Medicines", "Cardiovascular", "diuretic", 120, 1, 0, 0, "lasix|laasix|furosemide"),  # noqa: E501

    # ── Respiratory and allergy ──────────────────────────────────────────
    ("Ventolin Inhaler", "Salbutamol", "100mcg", "inhaler", "200 doses", 200, "GSK Pakistan", "Medicines", "Respiratory", "bronchodilator", 790, 0, 0, 0, "ventolin|ventoline|inhaler|salbutamol|سانس کا پمپ"),  # noqa: E501
    ("Seretide Accuhaler", "Salmeterol + Fluticasone", "50/250mcg", "inhaler", "60 doses", 60, "GSK Pakistan", "Medicines", "Respiratory", "combination inhaler", 3250, 1, 0, 0, "seretide|accuhaler|seritide"),  # noqa: E501
    ("Ventolin Respirator Solution", "Salbutamol", "5mg/ml", "solution", "15ml bottle", 1, "GSK Pakistan", "Medicines", "Respiratory", "bronchodilator", 340, 0, 0, 0, "ventolin solution|nebulizer solution"),  # noqa: E501
    ("Softin", "Cetirizine", "10mg", "tablet", "10 tablets", 10, "Hilton Pharma", "Medicines", "Allergy", "antihistamine", 95, 0, 0, 0, "softin|softeen|cetirizine|allergy|الرجی"),  # noqa: E501
    ("Zyrtec", "Cetirizine", "10mg", "tablet", "10 tablets", 10, "GSK Pakistan", "Medicines", "Allergy", "antihistamine", 175, 0, 0, 0, "zyrtec|zertec|zirtek"),  # noqa: E501
    ("Avil", "Pheniramine Maleate", "25mg", "tablet", "20 tablets", 20, "Sanofi-Aventis Pakistan", "Medicines", "Allergy", "antihistamine", 105, 0, 0, 0, "avil|aavil|pheniramine"),  # noqa: E501
    ("Arinac Forte", "Ibuprofen + Pseudoephedrine", "400mg", "tablet", "20 tablets", 20, "Abbott Pakistan", "Medicines", "Cough & Cold", "decongestant", 320, 0, 0, 0, "arinac|arinak|arinac forte"),  # noqa: E501
    ("Actifed", "Triprolidine + Pseudoephedrine", "-", "syrup", "60ml bottle", 1, "GSK Pakistan", "Medicines", "Cough & Cold", "cough syrup", 285, 0, 0, 0, "actifed|actifid|khansi ka syrup|کھانسی"),  # noqa: E501
    ("Hydrillin", "Diphenhydramine + Ammonium Chloride", "-", "syrup", "120ml bottle", 1, "Wyeth Pakistan", "Medicines", "Cough & Cold", "cough syrup", 235, 0, 0, 0, "hydrillin|hydrilin"),  # noqa: E501
    ("Otrivin", "Xylometazoline", "0.1%", "nasal spray", "10ml", 1, "GSK Pakistan", "Medicines", "Cough & Cold", "decongestant", 320, 0, 0, 0, "otrivin|otrivine|nasal spray|ناک کا سپرے"),  # noqa: E501
    ("Joshanda", "Herbal Extract Blend", "-", "sachet", "30 sachets", 30, "Hamdard Pakistan", "Medicines", "Cough & Cold", "herbal", 420, 0, 0, 0, "joshanda|johar joshanda|جوشاندہ"),  # noqa: E501

    # ── Thyroid, steroid, neuro ──────────────────────────────────────────
    ("Thyronorm", "Levothyroxine", "50mcg", "tablet", "100 tablets", 100, "Abbott Pakistan", "Medicines", "Endocrine", "thyroid", 620, 1, 0, 0, "thyronorm|thyroid|levothyroxine|تھائیرائیڈ"),  # noqa: E501
    ("Eltroxin", "Levothyroxine", "100mcg", "tablet", "100 tablets", 100, "GSK Pakistan", "Medicines", "Endocrine", "thyroid", 740, 1, 0, 0, "eltroxin|eltroxine"),  # noqa: E501
    ("Deltacortril", "Prednisolone", "5mg", "tablet", "20 tablets", 20, "Pfizer Pakistan", "Medicines", "Steroids", "corticosteroid", 185, 1, 0, 0, "deltacortril|prednisolone|steroid"),  # noqa: E501
    ("Xanax", "Alprazolam", "0.25mg", "tablet", "30 tablets", 30, "Pfizer Pakistan", "Medicines", "Neuro & Psych", "benzodiazepine", 480, 1, 1, 0, "xanax|zanax|alprazolam"),  # noqa: E501
    ("Lexotanil", "Bromazepam", "3mg", "tablet", "30 tablets", 30, "Roche Pakistan", "Medicines", "Neuro & Psych", "benzodiazepine", 520, 1, 1, 0, "lexotanil|lexotanil 3|bromazepam"),  # noqa: E501
    ("Rivotril", "Clonazepam", "0.5mg", "tablet", "30 tablets", 30, "Roche Pakistan", "Medicines", "Neuro & Psych", "benzodiazepine", 460, 1, 1, 0, "rivotril|revotril|clonazepam"),  # noqa: E501
    ("Tegral", "Carbamazepine", "200mg", "tablet", "50 tablets", 50, "Novartis Pakistan", "Medicines", "Neuro & Psych", "anticonvulsant", 540, 1, 0, 0, "tegral|tegretol|carbamazepine|مرگی"),  # noqa: E501
    ("Lexapro", "Escitalopram", "10mg", "tablet", "14 tablets", 14, "Highnoon Laboratories", "Medicines", "Neuro & Psych", "ssri", 680, 1, 0, 0, "lexapro|escitalopram|sozin"),  # noqa: E501

    # ── Vitamins and supplements ─────────────────────────────────────────
    ("Neurobion", "Vitamin B1 + B6 + B12", "-", "injection", "3 ampoules", 3, "Merck Pakistan", "Nutrition & Supplements", "Vitamins", "vitamin", 465, 0, 0, 0, "neurobion|nurobion|b12 injection|نیوروبیون"),  # noqa: E501
    ("Neurobion Tablets", "Vitamin B Complex", "-", "tablet", "30 tablets", 30, "Merck Pakistan", "Nutrition & Supplements", "Vitamins", "vitamin", 520, 0, 0, 0, "neurobion tablet|b complex"),  # noqa: E501
    ("Surbex Z", "Multivitamin + Zinc", "-", "tablet", "30 tablets", 30, "Abbott Pakistan", "Nutrition & Supplements", "Vitamins", "multivitamin", 690, 0, 0, 0, "surbex|surbex z|surbez|multivitamin"),  # noqa: E501
    ("CaC-1000 Plus", "Calcium + Vitamin C + D3", "-", "effervescent tablet", "10 tablets", 10, "Novartis Pakistan", "Nutrition & Supplements", "Minerals", "calcium", 420, 0, 0, 0, "cac 1000|cac1000|calcium|کیلشیم"),  # noqa: E501
    ("Qalsan D", "Calcium Carbonate + Vitamin D", "-", "chewable tablet", "30 tablets", 30, "Getz Pharma", "Nutrition & Supplements", "Minerals", "calcium", 360, 0, 0, 0, "qalsan|qalsan d|calcium chewable"),  # noqa: E501
    ("Sangobion", "Iron + Folic Acid + B12", "-", "capsule", "30 capsules", 30, "Merck Pakistan", "Nutrition & Supplements", "Minerals", "iron", 580, 0, 0, 0, "sangobion|sangobin|iron|خون کی کمی"),  # noqa: E501
    ("Folic Acid", "Folic Acid", "5mg", "tablet", "20 tablets", 20, "Zafa Pharmaceutical", "Nutrition & Supplements", "Vitamins", "vitamin", 85, 0, 0, 0, "folic acid|folic|فولک ایسڈ"),  # noqa: E501
    ("Ostrolic", "Vitamin D3", "200,000 IU", "injection", "1 ampoule", 1, "Pharmevo", "Nutrition & Supplements", "Vitamins", "vitamin", 320, 0, 0, 0, "ostrolic|vitamin d injection|d3"),  # noqa: E501
    ("Nutrifactor Vitamin D3", "Cholecalciferol", "5000 IU", "softgel", "60 softgels", 60, "Nutrifactor", "Nutrition & Supplements", "Vitamins", "vitamin", 1250, 0, 0, 0, "nutrifactor|vitamin d3|d3 softgel"),  # noqa: E501
    ("Ensure Vanilla", "Adult Nutrition Powder", "-", "powder", "400g tin", 1, "Abbott Pakistan", "Nutrition & Supplements", "Adult Nutrition", "nutrition", 3450, 0, 0, 0, "ensure|ensur|vanilla ensure"),  # noqa: E501
    ("Glucerna", "Diabetic Nutrition Powder", "-", "powder", "400g tin", 1, "Abbott Pakistan", "Nutrition & Supplements", "Adult Nutrition", "nutrition", 4100, 0, 0, 0, "glucerna|glucerna sr|diabetic milk"),  # noqa: E501

    # ── Baby and mother ──────────────────────────────────────────────────
    ("Pediasure Vanilla", "Child Nutrition Powder", "-", "powder", "400g tin", 1, "Abbott Pakistan", "Baby & Mother Care", "Child Nutrition", "nutrition", 3980, 0, 0, 0, "pediasure|pedia sure|pediashore"),  # noqa: E501
    ("S-26 Gold Stage 1", "Infant Formula", "-", "powder", "400g tin", 1, "Wyeth Nutrition Pakistan", "Baby & Mother Care", "Infant Formula", "formula", 2450, 0, 0, 0, "s26|s-26|s26 gold|formula milk"),  # noqa: E501
    ("Nan Pro 1", "Infant Formula", "-", "powder", "400g tin", 1, "Nestle Pakistan", "Baby & Mother Care", "Infant Formula", "formula", 2680, 0, 0, 0, "nan|nan pro|nan 1|نین"),  # noqa: E501
    ("Lactogen 1", "Infant Formula", "-", "powder", "400g tin", 1, "Nestle Pakistan", "Baby & Mother Care", "Infant Formula", "formula", 1890, 0, 0, 0, "lactogen|lactogin|lactogen 1"),  # noqa: E501
    ("Cerelac Wheat", "Infant Cereal", "-", "powder", "350g box", 1, "Nestle Pakistan", "Baby & Mother Care", "Baby Food", "food", 1150, 0, 0, 0, "cerelac|serelac|cerelac wheat"),  # noqa: E501
    ("Pampers Baby Dry Large", "Baby Diapers", "Large", "diapers", "62 pieces", 62, "Procter & Gamble Pakistan", "Baby & Mother Care", "Diapers", "diaper", 3250, 0, 0, 0, "pampers|pemper|diaper|ڈائپر"),  # noqa: E501
    ("Canbebe Medium", "Baby Diapers", "Medium", "diapers", "60 pieces", 60, "Hayat Kimya Pakistan", "Baby & Mother Care", "Diapers", "diaper", 2190, 0, 0, 0, "canbebe|can bebe|kanbebe"),  # noqa: E501
    ("Molfix Small", "Baby Diapers", "Small", "diapers", "68 pieces", 68, "Hayat Kimya Pakistan", "Baby & Mother Care", "Diapers", "diaper", 1980, 0, 0, 0, "molfix|molfex"),  # noqa: E501
    ("Johnson's Baby Shampoo", "Baby Shampoo", "-", "liquid", "200ml bottle", 1, "Johnson & Johnson Pakistan", "Baby & Mother Care", "Baby Care", "personal care", 620, 0, 0, 0, "johnsons|johnson baby|baby shampoo"),  # noqa: E501

    # ── Topical and dermatology ──────────────────────────────────────────
    ("Betnovate-N", "Betamethasone + Neomycin", "-", "cream", "20g tube", 1, "GSK Pakistan", "Medicines", "Dermatology", "topical steroid", 195, 0, 0, 0, "betnovate|betnovate n|betnovet|کریم"),  # noqa: E501
    ("Dermovate", "Clobetasol Propionate", "0.05%", "cream", "25g tube", 1, "GSK Pakistan", "Medicines", "Dermatology", "topical steroid", 340, 1, 0, 0, "dermovate|dermovat|clobetasol"),  # noqa: E501
    ("Canesten", "Clotrimazole", "1%", "cream", "20g tube", 1, "Bayer Pakistan", "Medicines", "Dermatology", "antifungal", 380, 0, 0, 0, "canesten|kanesten|clotrimazole|فنگس"),  # noqa: E501
    ("Polyfax", "Polymyxin B + Bacitracin", "-", "ointment", "20g tube", 1, "GSK Pakistan", "Medicines", "Dermatology", "topical antibiotic", 285, 0, 0, 0, "polyfax|polyfex|ointment"),  # noqa: E501
    ("Betadine Solution", "Povidone Iodine", "10%", "solution", "60ml bottle", 1, "Mundipharma Pakistan", "Medicines", "First Aid", "antiseptic", 320, 0, 0, 0, "betadine|betadin|antiseptic|زخم"),  # noqa: E501
    ("Burnol", "Aminacrine + Cetrimide", "-", "cream", "20g tube", 1, "Reckitt Benckiser Pakistan", "Medicines", "First Aid", "burn cream", 210, 0, 0, 0, "burnol|burnal|jalne ki cream"),  # noqa: E501

    # ── Eye and ear ──────────────────────────────────────────────────────
    ("Refresh Tears", "Carboxymethylcellulose", "0.5%", "eye drops", "10ml bottle", 1, "Allergan Pakistan", "Medicines", "Eye Care", "lubricant", 690, 0, 0, 0, "refresh tears|eye drops|آنکھوں کے قطرے"),  # noqa: E501
    ("Tobrex", "Tobramycin", "0.3%", "eye drops", "5ml bottle", 1, "Novartis Pakistan", "Medicines", "Eye Care", "antibiotic drops", 480, 1, 0, 0, "tobrex|tobrax|tobramycin"),  # noqa: E501
    ("Sofradex", "Framycetin + Dexamethasone", "-", "ear drops", "8ml bottle", 1, "Sanofi-Aventis Pakistan", "Medicines", "Ear Care", "ear drops", 420, 1, 0, 0, "sofradex|safradex|ear drops|کان کے قطرے"),  # noqa: E501

    # ── Vaccines (cold chain) ────────────────────────────────────────────
    ("Influvac Tetra", "Influenza Vaccine", "-", "injection", "1 pre-filled syringe", 1, "Abbott Pakistan", "Medicines", "Vaccines", "vaccine", 2850, 1, 0, 1, "influvac|flu vaccine|فلو ویکسین"),  # noqa: E501
    ("Engerix-B", "Hepatitis B Vaccine", "20mcg", "injection", "1 vial", 1, "GSK Pakistan", "Medicines", "Vaccines", "vaccine", 1950, 1, 0, 1, "engerix|hepatitis b|hep b vaccine"),  # noqa: E501
    ("Tetanus Toxoid", "Tetanus Vaccine", "-", "injection", "1 ampoule", 1, "NIH Pakistan", "Medicines", "Vaccines", "vaccine", 350, 1, 0, 1, "tetanus|tt injection|ٹیٹنس"),  # noqa: E501

    # ── Devices ──────────────────────────────────────────────────────────
    ("Omron HEM-7121", "Digital BP Monitor", "-", "device", "1 unit", 1, "Omron Healthcare", "Devices & Support", "Monitoring", "device", 13500, 0, 0, 0, "omron|bp machine|bp apparatus|blood pressure machine|بی پی مشین"),  # noqa: E501
    ("Certeza Nebulizer", "Compressor Nebulizer", "-", "device", "1 unit", 1, "Certeza Pakistan", "Devices & Support", "Respiratory Devices", "device", 7800, 0, 0, 0, "nebulizer|certeza|nebuliser|نیبولائزر"),  # noqa: E501
    ("Digital Thermometer", "Digital Thermometer", "-", "device", "1 unit", 1, "Certeza Pakistan", "Devices & Support", "Monitoring", "device", 690, 0, 0, 0, "thermometer|thermometre|بخار ناپنے والا"),  # noqa: E501
    ("Pulse Oximeter", "Fingertip Pulse Oximeter", "-", "device", "1 unit", 1, "Certeza Pakistan", "Devices & Support", "Monitoring", "device", 3400, 0, 0, 0, "oximeter|oxymeter|oxygen meter|آکسیمیٹر"),  # noqa: E501
    ("Wheelchair Standard", "Folding Wheelchair", "-", "device", "1 unit", 1, "Surgimed Pakistan", "Devices & Support", "Mobility", "device", 24500, 0, 0, 0, "wheelchair|wheel chair|ویل چیئر"),  # noqa: E501
    ("Nebulizer Mask Adult", "Nebulizer Mask Kit", "Adult", "accessory", "1 kit", 1, "Surgimed Pakistan", "Devices & Support", "Respiratory Devices", "accessory", 380, 0, 0, 0, "nebulizer mask|mask kit"),  # noqa: E501
    ("Surgical Face Mask", "3-Ply Face Mask", "-", "mask", "50 pieces", 50, "Shield Corporation", "Devices & Support", "Protection", "consumable", 320, 0, 0, 0, "mask|surgical mask|face mask|ماسک"),  # noqa: E501

    # ── Personal care ────────────────────────────────────────────────────
    ("Sensodyne Repair", "Toothpaste", "-", "paste", "75g tube", 1, "GSK Pakistan", "Personal Care", "Oral Care", "oral care", 780, 0, 0, 0, "sensodyne|sensoden|toothpaste|دانت"),  # noqa: E501
    ("Colgate Total", "Toothpaste", "-", "paste", "100g tube", 1, "Colgate-Palmolive Pakistan", "Personal Care", "Oral Care", "oral care", 420, 0, 0, 0, "colgate|kolgate|toothpaste"),  # noqa: E501
    ("Dettol Antiseptic", "Chloroxylenol", "4.8%", "liquid", "250ml bottle", 1, "Reckitt Benckiser Pakistan", "Personal Care", "Antiseptic", "antiseptic", 690, 0, 0, 0, "dettol|detol|antiseptic liquid|ڈیٹول"),  # noqa: E501
    ("Safeguard Soap", "Antibacterial Soap", "-", "soap", "3 x 130g", 3, "Procter & Gamble Pakistan", "Personal Care", "Bath", "soap", 480, 0, 0, 0, "safeguard|safe guard|soap"),  # noqa: E501
    ("Head & Shoulders", "Anti-Dandruff Shampoo", "-", "liquid", "360ml bottle", 1, "Procter & Gamble Pakistan", "Personal Care", "Hair Care", "shampoo", 1150, 0, 0, 0, "head and shoulders|h&s|shampoo|خشکی"),  # noqa: E501
    ("Nivea Soft Cream", "Moisturising Cream", "-", "cream", "200ml jar", 1, "Beiersdorf Pakistan", "Beauty Care", "Skin Care", "moisturiser", 890, 0, 0, 0, "nivea|nivia|moisturizer"),  # noqa: E501
    ("Always Ultra", "Sanitary Pads", "Long", "pads", "16 pads", 16, "Procter & Gamble Pakistan", "Personal Care", "Feminine Care", "hygiene", 520, 0, 0, 0, "always|olways|pads|sanitary"),  # noqa: E501

    # ── Foods and beverages ──────────────────────────────────────────────
    ("Nestle Fruita Vitals Red Grape", "Fruit Juice", "-", "juice", "1 litre", 1, "Nestle Pakistan", "Foods & Beverages", "Juices", "beverage", 380, 0, 0, 0, "fruita vitals|juice|nestle juice"),  # noqa: E501
    ("Tapal Danedar", "Black Tea", "-", "loose tea", "475g pack", 1, "Tapal Tea", "Foods & Beverages", "Tea & Coffee", "beverage", 1290, 0, 0, 0, "tapal|danedar|chai|چائے"),  # noqa: E501
    ("Quaker Oats", "Rolled Oats", "-", "cereal", "500g box", 1, "PepsiCo Pakistan", "Foods & Beverages", "Breakfast", "food", 890, 0, 0, 0, "quaker|oats|jai ka dalia"),  # noqa: E501
]

# ── Names for generated people ───────────────────────────────────────────
# Pakistani names, urban Karachi/Lahore weighting. Urdu spellings for the
# greeting: the agent says the caller's name aloud, and a transliterated
# "Ayesha" read by an Urdu voice sounds wrong.
FIRST_NAMES_M: list[tuple[str, str]] = [
    ("Ahmed", "احمد"), ("Bilal", "بلال"), ("Hassan", "حسن"), ("Usman", "عثمان"),
    ("Faisal", "فیصل"), ("Imran", "عمران"), ("Kashif", "کاشف"), ("Adnan", "عدنان"),
    ("Zeeshan", "ذیشان"), ("Tariq", "طارق"), ("Salman", "سلمان"), ("Rehan", "ریحان"),
    ("Junaid", "جنید"), ("Nabeel", "نبیل"), ("Owais", "اویس"), ("Shahzad", "شہزاد"),
    ("Danish", "دانش"), ("Waqar", "وقار"), ("Asad", "اسد"), ("Hamza", "حمزہ"),
]
FIRST_NAMES_F: list[tuple[str, str]] = [
    ("Ayesha", "عائشہ"), ("Fatima", "فاطمہ"), ("Sana", "ثنا"), ("Hina", "حنا"),
    ("Maria", "ماریہ"), ("Zainab", "زینب"), ("Rabia", "رابعہ"), ("Saima", "صائمہ"),
    ("Nadia", "نادیہ"), ("Amna", "آمنہ"), ("Sadia", "سعدیہ"), ("Mehwish", "مہوش"),
    ("Kiran", "کرن"), ("Anum", "انم"), ("Hira", "حرا"), ("Farah", "فرح"),
    ("Sidra", "سدرہ"), ("Uzma", "عظمیٰ"), ("Nimra", "نمرہ"), ("Bushra", "بشریٰ"),
]
LAST_NAMES: list[tuple[str, str]] = [
    ("Khan", "خان"), ("Ahmed", "احمد"), ("Ali", "علی"), ("Siddiqui", "صدیقی"),
    ("Sheikh", "شیخ"), ("Malik", "ملک"), ("Qureshi", "قریشی"), ("Hussain", "حسین"),
    ("Raza", "رضا"), ("Abbasi", "عباسی"), ("Butt", "بٹ"), ("Chaudhry", "چوہدری"),
    ("Farooqui", "فاروقی"), ("Memon", "میمن"), ("Jamil", "جمیل"), ("Iqbal", "اقبال"),
]

DOCTORS = [
    ("Dr. Salman Ahmed", "Aga Khan University Hospital"),
    ("Dr. Farhana Siddiqui", "Liaquat National Hospital"),
    ("Dr. Naveed Alam", "Ziauddin Hospital"),
    ("Dr. Ayesha Tariq", "South City Hospital"),
    ("Dr. Kamran Rizvi", "Jinnah Postgraduate Medical Centre"),
    ("Dr. Sadaf Hussain", "Shaukat Khanum Memorial Hospital"),
    ("Dr. Imtiaz Bhatti", "Services Hospital Lahore"),
    ("Dr. Rukhsana Aziz", "Fatima Memorial Hospital"),
    ("Dr. Owais Rehman", "Indus Hospital"),
    ("Dr. Nida Shah", "Nishtar Hospital Multan"),
]

COURIERS = ["DVAGO Rider", "TCS", "Leopards Courier", "M&P", "Trax"]

MOBILE_PREFIXES = ["0300", "0301", "0302", "0311", "0312", "0321", "0322",
                   "0331", "0333", "0334", "0335", "0341", "0345", "0348"]
