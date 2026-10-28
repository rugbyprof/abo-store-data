#!/usr/bin/env python3
"""Build a class-sized mock-store catalog from Amazon Berkeley Objects (ABO).

Reads the ABO listings + image metadata (read-only), picks a deterministic,
category-balanced sample of English products, generates the fields ABO lacks
(price, stock, dates, customers, orders) and writes a self-contained bundle:

    <out>/schema.sql             Postgres DDL
    <out>/load_postgres.sql      psql script: schema + \\copy of every seed CSV
    <out>/seed/*.csv             COPY-able CSVs (header row, CSV format)
    <out>/mongo/products.ndjson  one full product document per line (mongoimport)
    <out>/images/<xx>/<id>.jpg   only the images the selected products use
    <out>/README.md, LICENSE-CC-BY-4.0.txt

Everything random is derived from --seed and the item_id, so every student who
runs the same command gets byte-identical output. Standard library only.

    python3 build_catalog.py                       # 5,000 products -> ./dist
    python3 build_catalog.py --products 1000 --out small
"""

import argparse
import csv
import gzip
import hashlib
import json
import math
import random
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_ABO = Path("/Users/griffin/Data/abo-images-small")
DEFAULT_NAMES = Path(
    "/Users/griffin/Library/Mobile Documents/com~apple~CloudDocs/data/unique_first_last_names.json"
)
HERE = Path(__file__).resolve().parent

PHONE_CASE = "CELLULAR_PHONE_CASE"

# Product catalog dates, then order dates after them, so no order predates its product.
PRODUCT_START = datetime(2023, 1, 1, tzinfo=timezone.utc)
PRODUCT_END = datetime(2024, 6, 30, tzinfo=timezone.utc)
CUSTOMER_START = datetime(2023, 6, 1, tzinfo=timezone.utc)
ORDER_START = datetime(2024, 7, 1, tzinfo=timezone.utc)
ORDER_END = datetime(2025, 12, 31, tzinfo=timezone.utc)

# ---------------------------------------------------------------------------
# Departments (top-level categories) and price levels
# ---------------------------------------------------------------------------

# (department, median price in dollars, words). A product_type like SAUTE_FRY_PAN is split
# into words; the LAST word (the head noun) is tried first against every department,
# then the earlier words. Plural "S" is ignored. EXACT/LEAD below handle the oddballs.
DEPARTMENTS = [
    ("Phone & Device Accessories", 15, "PHONE SCREEN_PROTECTOR HEADPHONE MICROPHONE"),
    ("Jewelry & Watches", 40, "FINERING FINENECKLACEBRACELETANKLET FINEEARRING FINEOTHER FASHIONRING "
                              "FASHIONNECKLACEBRACELETANKLET FASHIONEARRING FASHIONOTHER RING NECKLACE "
                              "EARRING BRACELET WATCH JEWELRY CHARM"),
    ("Kitchen & Dining", 25, "KITCHEN CUP JAR COOKER OVEN BAKEWARE DINNERWARE DISHWARE CUTLERY FLATWARE "
                             "BOTTLE MUG BOWL PLATE PAN PLACEMAT UTENSIL KNIFE PITCHER TOASTER BLENDER "
                             "JUICER PROCESSOR MIXER SLICER DEHYDRATOR STRAW COASTER OPENER CUTTER MOLD "
                             "BOILER MICROWAVE SETTING CASSEROLE THERMOS FRYER BURNER TRAY GRILL REFRIGERATOR GRIDDLE TIMER"),
    ("Grocery", 8, "GROCERY COFFEE TEA HERB LEGUME BREAD SNACK CANDY BEVERAGE SPICE CEREAL CHOCOLATE "
                   "FRUIT VEGETABLE CONDIMENT SAUCE PASTA CHEESE MEAT JUICE SUGAR FLOUR OIL SEED "
                   "GRAIN RICE SOUP STEW DAIRY DRINK CAKE CRACKER NOODLE POPCORN WINE MILK CREAM "
                   "COOKIE PRODUCE SEAFOOD EGG YOGURT NUT BUTTER HONEY VINEGAR PASTRY PRETZEL POULTRY "
                   "FISH PANTRY MIX CONCENTRATE SALAD BAR WATER JERKY SHELLFISH SPIRIT LEAVENING THICKENING"),
    ("Health & Beauty", 18, "HEALTH BEAUTY VITAMIN SUPPLEMENT SUPPLEMENTS SKIN HAIR HAIRBAND SHAMPOO "
                            "MAKEUP CARE COSMETIC LOTION SOAP DEODORANT FRAGRANCE SUNSCREEN TOOTH "
                            "TOOTHBRUSH MOUTHWASH MEDICATION LIP TISSUE BABY MOISTURIZER EXFOLIANT "
                            "CONCEALER POWDER MASK BRUSH COMB IRON RAZOR DRUGSTORE HEALTHCARE BRACE "
                            "DRESSING LUBRICANT EYELID ASTRINGENT CONDITIONER EARPLUG THERMOMETER MASSAGER MASSAGE NAIL MASCARA EYEBROW PACIFIER"),
    ("Fashion", 45, "SHOE BOOT SANDAL SLIPPER HANDBAG BACKPACK SUITCASE LUGGAGE SHIRT PANT DRESS HAT "
                    "SOCK WALLET COAT SWEATER SKIRT SHORT BRA UNDERWEAR APPAREL BELT SCARF SUNGLASSE "
                    "BAG TOTE JACKET LEGGING SWIMWEAR EYEWEAR UMBRELLA LEOTARD GLOVE NECKTIE SUSPENDER "
                    "SUIT COSTUME INSERT SWEATBAND EARMUFF VEST OVERALL CINCHER CHOLI"),
    ("Furniture", 180, "CHAIR SOFA TABLE STOOL SEATING OTTOMAN HEADBOARD SHELF CABINET DESK FURNITURE "
                       "BENCH DRESSER BED MATTRESS BOOKCASE"),
    ("Home & Decor", 35, "HOME RUG WALL ART PILLOW LAMP LIGHT BULB FIXTURE LIGHTING PLANTER CURTAIN SHEET "
                         "BLANKET MIRROR VASE CLOCK TOWEL DECOR CANDLE QUILT COMFORTER DUVET BASKET "
                         "FRAME STORAGE HOOK RACK HANGER LAUNDRY HAMPER ORGANIZER APPLIANCE APPLIANCES "
                         "DETERGENT SHADE INCENSE DEODORIZER HUMIDIFIER TRASH CAN BUCKET DOORSTOP "
                         "SHOWERHEAD TOILET SEAT HOLDER LIVING GARDEN FEEDER LIGHTER WALLPAPER DEHUMIDIFIER PURIFIER FIGURINE SCULPTURE BOOKEND DIVIDER FOUNTAIN CADDY VINE WASHER TREE PLANT"),
    ("Office & School", 20, "OFFICE PAPER PEN NOTEBOOK BINDER WRITING SCHOOL LABEL FOLDER STAPLER ENVELOPE "
                            "SCISSOR STOCK STICKER EDUCATIONAL TEACHING INK TONER BOARD NOTE PINBOARD CALCULATOR CRAFT"),
    ("Sports & Outdoors", 35, "SPORT SPORTING OUTDOOR EXERCISE FITNESS BICYCLE CAMP YOGA BALL RACKET TENT "
                              "GOLF BOXING PUNCHING SLEEPING ROLLER RECREATION SHELTER STOVE FLOTATION BARBELL SKATEBOARD FISHING ROWING TREADMILL SCOOTER CYCLING SWING BINOCULAR TELESCOPE WALKING"),
    ("Pet Supplies", 22, "PET DOG CAT"),
    ("Toys & Games", 20, "TOY TOYS GAME GAMES SLIME PUZZLE DICE BLOCK"),
    ("Electronics", 30, "ELECTRONIC ELECTRONICS CABLE CHARGER COMPUTER SPEAKER BATTERY CAMERA KEYBOARD "
                        "MOUSE TELEVISION AUDIO FAN CONDITIONER DISPLAY ADAPTER HUB FILAMENT PRINTER "
                        "PROJECTOR REMOTE ANTENNA MEMORY MEDIA DRIVE DEVICE TRIPOD LENS FLASH SWITCH "
                        "STATION STRIP POWER RECORDING INSTRUMENT INSTRUMENTS RADIO CAMCORDER RECEIVER AMPLIFIER SCANNER MONITOR ROUTER PLAYER"),
    ("Tools, Auto & Home Improvement", 25, "HARDWARE TOOL TOOLS JANITORIAL CLEANING CLEANER FAUCET "
                                           "PLUMBING ELECTRICAL PAINT VACUUM SAFETY SAFE LOCK PADLOCK "
                                           "MECHANICAL COMPONENT FASTENER SCREW SCREWDRIVER WRENCH PLIER "
                                           "DRILL BIT LADDER SEAL GAUGE TUBING MULTITOOL CLAMP VISE "
                                           "PART PUMP STRAP WHEEL SCALE BROOM BOX SUPPLY SUPPLIES "
                                           "GLASSES PURIFICATION SEALER VALVE SAW BLADE SHOVEL SPADE COMPRESSOR MATERIAL CART WAGON"),
]
# Whole product types that the word rules would get wrong.
EXACT = {
    "ACCESSORY": "Fashion", "ACCESSORY_OR_PART_OR_SUPPLY": "Fashion", "CHANGING_PAD_COVER": "Health & Beauty",
    "BEAN_BAG_CHAIR": "Furniture", "STEERING_WHEEL_COVER": "Tools, Auto & Home Improvement",
    "INFANT_TODDLER_CAR_SEAT": "Health & Beauty", "PERSONAL_PILL_DISPENSER": "Health & Beauty",
    "PUMP_DISPENSER": "Home & Decor", "BATHWATER_ADDITIVE": "Health & Beauty",
    "WATER_PURIFICATION_UNIT": "Kitchen & Dining", "FOOD_STORAGE_BAG": "Kitchen & Dining",
    "FOOD_SERVICE_SUPPLY": "Kitchen & Dining", "STORAGE_BINDER": "Office & School",
    "TOILET_PAPER_HOLDER": "Home & Decor", "PAPER_TOWEL_HOLDER": "Home & Decor",
    "BAKING_PAPER": "Kitchen & Dining", "MEASURING_CUP": "Kitchen & Dining",
    "STRING_LIGHT": "Home & Decor", "SWATCH": "Everything Else",
    "WASTE_BAG": "Home & Decor", "STORAGE_BAG": "Home & Decor", "COSMETIC_CASE": "Health & Beauty",
    "TECHNICAL_SPORT_SHOE": "Sports & Outdoors", "FITNESS_BENCH": "Sports & Outdoors",
    "SPORT_TABLE_GAME": "Toys & Games", "TABLETOP_GAME": "Toys & Games",
    "PORTABLE_TOOL_BOX": "Tools, Auto & Home Improvement", "SHIPPING_BOX": "Office & School",
    "MEAL_HOLDER": "Kitchen & Dining", "WILDLIFE_FEEDER": "Pet Supplies", "PET_PEST_CONTROL": "Pet Supplies",
    "COFFEE_MAKER": "Kitchen & Dining", "BREAD_MAKING_MACHINE": "Kitchen & Dining",
    "ELECTRIC_WATER_BOILER": "Kitchen & Dining", "FOOD_PREPARATION_MOLD": "Kitchen & Dining",
    "LAB_SUPPLY": "Office & School", "MICROSCOPES": "Office & School",
    "SALWAR_SUIT_SET": "Fashion", "WOUND_DRESSING": "Health & Beauty",
    "MUSCLE_ROLLER": "Sports & Outdoors", "DRYING_RACK": "Home & Decor",
    "SCREEN_PROTECTOR": "Phone & Device Accessories", "AIR_CONDITIONER": "Home & Decor",
    "PERSONAL_COMPUTER": "Electronics", "PAINT_BRUSH": "Tools, Auto & Home Improvement",
    "CLEANING_BRUSH": "Home & Decor", "SALAD_DRESSING": "Grocery", "TOOTH_CLEANING_AGENT": "Health & Beauty",
    "WRITING_INSTRUMENT": "Office & School", "WATER_FLOTATION_DEVICE": "Sports & Outdoors",
    "PORTABLE_STOVE": "Sports & Outdoors", "CUTTING_BOARD": "Kitchen & Dining",
    "STORAGE_BOX": "Home & Decor", "EDUCATIONAL_SUPPLIES": "Office & School",
    "ART_AND_CRAFT_SUPPLY": "Office & School", "LITTER_BOX": "Pet Supplies", "ANIMAL_LITTER": "Pet Supplies",
    "ANIMAL_COLLAR": "Pet Supplies", "OUTDOOR_LIVING": "Home & Decor", "BED_FRAME": "Furniture",
    "PLUMBING_FIXTURE": "Tools, Auto & Home Improvement", "ESSENTIAL_OIL": "Health & Beauty",
    "HOME_BED_AND_BATH": "Home & Decor", "GOLF_CLUB_BAG": "Sports & Outdoors", "BOXING_GLOVE": "Sports & Outdoors",
    "SLEEPING_BAG": "Sports & Outdoors", "PUNCHING_BAG": "Sports & Outdoors",
    "INSTRUMENT_PARTS_AND_ACCESSORIES": "Everything Else", "STRINGED_INSTRUMENTS": "Everything Else",
    "PERCUSSION_INSTRUMENTS": "Everything Else", "BLOOD_PRESSURE_MONITOR": "Health & Beauty",
    "WASHER_DRYER_COMBINATION": "Home & Decor", "ICE_CHEST": "Sports & Outdoors",
}
# A leading word that decides the department regardless of the rest (AUTO_ACCESSORY, PET_APPAREL ...).
LEAD = {
    "AUTO": "Tools, Auto & Home Improvement", "VEHICLE": "Tools, Auto & Home Improvement",
    "CAR": "Tools, Auto & Home Improvement", "POWERSPORTS": "Tools, Auto & Home Improvement",
    "LICENSE": "Tools, Auto & Home Improvement", "CARGO": "Tools, Auto & Home Improvement",
    "TRANSPORT": "Sports & Outdoors", "PET": "Pet Supplies",
    "CE": "Electronics", "AMAZON": "Electronics", "COMPUTER": "Electronics", "CAMERA": "Electronics",
    "WIRELESS": "Phone & Device Accessories", "CELLULAR": "Phone & Device Accessories",
    "LANDLINE": "Phone & Device Accessories", "PORTABLE": "Electronics", "AV": "Electronics",
    "GPS": "Electronics", "VIDEO": "Electronics", "SECURITY": "Electronics", "NETWORK": "Electronics",
    "OFFICE": "Office & School", "KITCHEN": "Kitchen & Dining",
    "HOME": "Home & Decor", "HARDWARE": "Tools, Auto & Home Improvement", "PERSONAL": "Health & Beauty",
    "HAIR": "Health & Beauty", "SKIN": "Health & Beauty", "BODY": "Health & Beauty",
    "ABIS": None, "FASHION": "Jewelry & Watches", "FINE": "Jewelry & Watches",
    "JEWELRY": "Jewelry & Watches", "BABY": "Health & Beauty", "DISHWASHER": "Home & Decor",
    "MAJOR": "Home & Decor", "SMALL": "Home & Decor", "OUTDOOR": "Sports & Outdoors",
    "FURNITURE": "Furniture", "ORTHOPEDIC": "Health & Beauty", "PROFESSIONAL": "Health & Beauty",
}
OTHER_DEPARTMENT = ("Everything Else", 25)

# Per-type medians (dollars) where the department default would look silly.
TYPE_PRICE = {
    "CELLULAR_PHONE_CASE": 12, "SCREEN_PROTECTOR": 9,
    "FINENECKLACEBRACELETANKLET": 180, "FINERING": 260, "FINEEARRING": 150, "FINEOTHER": 150,
    "RING": 25, "NECKLACE": 30, "EARRING": 20, "WATCH": 120,
    "SHOES": 60, "BOOT": 95, "SANDAL": 40, "HANDBAG": 70, "BACKPACK": 45, "SUITCASE": 130,
    "GROCERY": 7, "COFFEE": 13, "TEA": 8, "HERB": 6, "LEGUME": 4, "BREAD": 5,
    "SOFA": 750, "CHAIR": 140, "BED": 450, "HEADBOARD": 220, "TABLE": 200, "STOOL_SEATING": 90,
    "OTTOMAN": 120, "DESK": 230, "CABINET": 210, "SHELF": 80, "MATTRESS": 400,
    "HOME_FURNITURE_AND_DECOR": 60, "HOME_BED_AND_BATH": 40, "FLAT_SHEET": 30,
    "RUG": 110, "WALL_ART": 55, "LAMP": 65, "LIGHT_FIXTURE": 110, "LIGHT_BULB": 12, "PILLOW": 25,
    "PLANTER": 30, "FURNITURE_COVER": 35,
    "VITAMIN": 18, "NUTRITIONAL_SUPPLEMENT": 22, "SKIN_CLEANING_AGENT": 12,
    "HARDWARE_HANDLE": 15, "JANITORIAL_SUPPLY": 14,
}


def _singular(word):
    return word[:-1] if len(word) > 3 and word.endswith("S") and not word.endswith("SS") else word


_DEPT_MEDIAN = {name: median for name, median, _ in DEPARTMENTS}
_DEPT_MEDIAN[OTHER_DEPARTMENT[0]] = OTHER_DEPARTMENT[1]
_WORDS = [(name, {_singular(w) for w in words.split()}) for name, _, words in DEPARTMENTS]


def department_for(product_type):
    """Map an ABO product_type to (department, median price in dollars)."""
    name = EXACT.get(product_type)
    words = product_type.split("_")
    if name is None and words[0] == "HOME" and len(words) > 1:
        words = words[1:]                      # HOME_FURNITURE_AND_DECOR -> furniture
    if name is None and words[0] in LEAD and LEAD[words[0]]:
        name = LEAD[words[0]]
    if name is None:
        if words[0] == "ABIS":
            words = words[1:]
        for word in reversed(words):           # head noun first
            word = _singular(word)
            name = next((d for d, ws in _WORDS if word in ws), None)
            if name:
                break
    name = name or OTHER_DEPARTMENT[0]
    return name, _DEPT_MEDIAN[name]


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def humanize(product_type):
    """CELLULAR_PHONE_CASE -> 'Cellular Phone Case'."""
    special = {"FINENECKLACEBRACELETANKLET": "Fine Necklaces, Bracelets & Anklets",
               "FINERING": "Fine Rings", "FINEEARRING": "Fine Earrings", "FINEOTHER": "Fine Jewelry"}
    return special.get(product_type) or product_type.replace("_", " ").title()


# ---------------------------------------------------------------------------
# Deterministic randomness
# ---------------------------------------------------------------------------

def rng_for(seed, *parts):
    """A Random whose state depends only on seed + parts (stable across Python versions)."""
    return random.Random(":".join([str(seed), *map(str, parts)]))


def hash_key(seed, item_id):
    return hashlib.sha256(f"{seed}:{item_id}".encode()).hexdigest()


def random_time(rng, start, end):
    span = (end - start).total_seconds()
    return start + timedelta(seconds=int(rng.random() * span))


def nice_price_cents(dollars):
    """Snap a raw price to a retail-looking ending (x.99, x.49, x9.99 ...)."""
    if dollars < 10:
        return max(199, int(math.floor(dollars)) * 100 + 99 - 100 * (dollars < 2))
    if dollars < 100:
        return int(math.floor(dollars)) * 100 + 99
    return int(round(dollars / 10.0)) * 1000 - 1


# ---------------------------------------------------------------------------
# Reading ABO
# ---------------------------------------------------------------------------

def lang_rank(tag):
    if tag == "en_US":
        return 0
    if tag and tag.startswith("en_"):
        return 1
    return 9


def english_values(field):
    """Values of a [{language_tag, value}] list in the best English tag present."""
    if not field:
        return []
    best = min(lang_rank(x.get("language_tag")) for x in field)
    if best > 1:
        return []
    return [x["value"] for x in field if lang_rank(x.get("language_tag")) == best and x.get("value") is not None]


def first_value(field, english_only=True):
    vals = english_values(field)
    if not vals and not english_only and field:
        vals = [x.get("value") for x in field if x.get("value") is not None]
    return clean(vals[0]) if vals else None


def clean(text):
    if text is None:
        return None
    text = re.sub(r"\s+", " ", str(text)).strip()
    return text or None


def unique_clean(values, limit=None, lower=False):
    out, seen = [], set()
    for v in values:
        v = clean(v)
        if not v:
            continue
        key = v.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(v.lower() if lower else v)
        if limit and len(out) >= limit:
            break
    return out


def load_images(abo):
    images = {}
    with open(abo / "images/metadata/images.csv", newline="") as f:
        for row in csv.DictReader(f):
            images[row["image_id"]] = (row["path"], int(row["height"]), int(row["width"]))
    return images


def listing_score(r):
    """Lower is better when the same item_id appears in several marketplaces."""
    name_rank = min((lang_rank(x.get("language_tag")) for x in r.get("item_name", [])), default=9)
    return (name_rank, r.get("domain_name") != "amazon.com", -len(r.get("other_image_id", [])))


def read_candidates(abo, images, small_dir):
    """Return {item_id: product dict} for usable English listings, best listing per item."""
    stats = {"listings": 0, "english_complete": 0}
    best = {}
    for path in sorted((abo / "listings/metadata").glob("listings_*.json.gz")):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                stats["listings"] += 1
                r = json.loads(line)
                if not (r.get("main_image_id") and r.get("brand") and r.get("product_type")):
                    continue
                if not english_values(r.get("item_name")) or not english_values(r.get("bullet_point")):
                    continue
                stats["english_complete"] += 1
                cur = best.get(r["item_id"])
                if cur is None or listing_score(r) < listing_score(cur):
                    best[r["item_id"]] = r
    stats["unique_items"] = len(best)

    products = {}
    missing_image = 0
    for item_id, r in best.items():
        main = r["main_image_id"]
        if main not in images or not (small_dir / images[main][0]).is_file():
            missing_image += 1
            continue
        products[item_id] = to_product(r)
    stats["missing_main_image"] = missing_image
    stats["candidates"] = len(products)
    return products, stats


def to_product(r):
    weight = None
    for w in r.get("item_weight", []):
        nv = w.get("normalized_value") or {}
        if nv.get("unit") == "pounds" and nv.get("value") is not None:
            weight = round(float(nv["value"]), 2)
            break
    english_domain = r.get("country") in {"US", "GB", "CA", "AU", "IN", "SG", "AE"}
    node_path = None
    if english_domain and r.get("node"):
        node_path = clean((r["node"][0].get("node_name") or "").lstrip("/"))
    dims = {}
    for axis, d in (r.get("item_dimensions") or {}).items():
        nv = d.get("normalized_value") or {}
        if nv.get("value") is not None:
            dims[axis] = {"value": nv["value"], "unit": nv.get("unit")}
    return {
        "item_id": r["item_id"],
        "name": first_value(r.get("item_name")),
        "brand": first_value(r.get("brand"), english_only=False),
        "product_type": r["product_type"][0]["value"],
        "color": first_value(r.get("color")),
        "weight_lb": weight,
        "bullets": unique_clean(english_values(r.get("bullet_point")), limit=10),
        "keywords": unique_clean(english_values(r.get("item_keywords")), limit=20, lower=True),
        "node_path": node_path,
        "source_domain": r.get("domain_name"),
        "main_image_id": r["main_image_id"],
        "other_image_ids": r.get("other_image_id", []),
        # Extra attributes only go to Mongo (they are sparse and vary by product type).
        "attributes": {k: v for k, v in {
            "material": first_value(r.get("material")),
            "style": first_value(r.get("style")),
            "pattern": first_value(r.get("pattern")),
            "fabric_type": first_value(r.get("fabric_type")),
            "finish_type": first_value(r.get("finish_type")),
            "item_shape": first_value(r.get("item_shape")),
            "model_name": first_value(r.get("model_name")),
            "model_number": first_value(r.get("model_number"), english_only=False),
            "model_year": (r.get("model_year") or [{}])[0].get("value"),
            "dimensions": dims or None,
            "description": first_value(r.get("product_description")),
        }.items() if v is not None},
    }


# ---------------------------------------------------------------------------
# Sampling and synthetic fields
# ---------------------------------------------------------------------------

def sample(products, target, seed, phone_share, type_share):
    """Hash-ordered greedy sample with per-type caps. Same inputs -> same set."""
    phone_cap = int(target * phone_share)
    type_cap = max(1, int(target * type_share))
    order = sorted(products, key=lambda i: hash_key(seed, i))
    taken, per_type = [], {}
    for item_id in order:
        pt = products[item_id]["product_type"]
        cap = phone_cap if pt == PHONE_CASE else type_cap
        if per_type.get(pt, 0) >= cap:
            continue
        per_type[pt] = per_type.get(pt, 0) + 1
        taken.append(item_id)
        if len(taken) >= target:
            break
    return sorted(taken)


def synth_product_fields(p, seed):
    rng = rng_for(seed, p["item_id"], "product")
    dept, dept_median = department_for(p["product_type"])
    median = TYPE_PRICE.get(p["product_type"], dept_median)
    dollars = median * math.exp(rng.gauss(0, 0.45))
    dollars = min(max(dollars, 1.99), 4999.99)
    roll = rng.random()
    if roll < 0.06:
        stock = 0                                  # out of stock
    elif roll < 0.18:
        stock = rng.randint(1, 9)                  # low stock
    else:
        stock = int(rng.lognormvariate(3.6, 0.8))  # typically 15-100
    p["department"] = dept
    p["price_cents"] = nice_price_cents(dollars)
    p["stock"] = stock
    p["created_at"] = random_time(rng, PRODUCT_START, PRODUCT_END)


def build_images(p, images, small_dir, max_images):
    rows, seen = [], set()
    for image_id in [p["main_image_id"], *p["other_image_ids"]]:
        if image_id in seen or image_id not in images:
            continue
        path, h, w = images[image_id]
        if not (small_dir / path).is_file():
            continue
        seen.add(image_id)
        rows.append({"image_id": image_id, "path": f"images/{path}", "src": path,
                     "orig_width": w, "orig_height": h, "is_main": not rows,
                     "sort_order": len(rows)})
        if len(rows) >= max_images:
            break
    return rows


US_PLACES = [
    ("Wichita Falls", "TX"), ("Dallas", "TX"), ("Austin", "TX"), ("Houston", "TX"), ("El Paso", "TX"),
    ("Oklahoma City", "OK"), ("Tulsa", "OK"), ("Albuquerque", "NM"), ("Denver", "CO"),
    ("Phoenix", "AZ"), ("Los Angeles", "CA"), ("San Diego", "CA"), ("Sacramento", "CA"),
    ("Seattle", "WA"), ("Portland", "OR"), ("Boise", "ID"), ("Salt Lake City", "UT"),
    ("Chicago", "IL"), ("Minneapolis", "MN"), ("Kansas City", "MO"), ("St. Louis", "MO"),
    ("Nashville", "TN"), ("Atlanta", "GA"), ("Miami", "FL"), ("Orlando", "FL"),
    ("Charlotte", "NC"), ("Richmond", "VA"), ("New York", "NY"), ("Boston", "MA"),
    ("Philadelphia", "PA"), ("Pittsburgh", "PA"), ("Columbus", "OH"), ("Detroit", "MI"),
    ("New Orleans", "LA"), ("Little Rock", "AR"), ("Omaha", "NE"), ("Anchorage", "AK"), ("Honolulu", "HI"),
]


def build_customers(names_path, count, seed):
    names = json.loads(Path(names_path).read_text())
    firsts, lasts = sorted(set(names["first_names"])), sorted(set(names["last_names"]))
    rng = rng_for(seed, "customers")
    pairs, seen, emails = [], set(), set()
    while len(pairs) < count:
        first, last = rng.choice(firsts), rng.choice(lasts)
        if (first, last) in seen:
            continue
        seen.add((first, last))
        base = f"{first}.{last}".lower().replace(" ", "").replace("'", "")
        email, n = f"{base}@example.com", 1
        while email in emails:
            n += 1
            email = f"{base}{n}@example.com"
        emails.add(email)
        city, state = rng.choice(US_PLACES)
        pairs.append({
            "customer_id": len(pairs) + 1, "first_name": first, "last_name": last,
            "email": email, "city": city, "state": state,
            "created_at": random_time(rng, CUSTOMER_START, ORDER_END - timedelta(days=30)),
        })
    return pairs


def build_orders(customers, products, count, seed):
    """Orders with a long-tail product popularity so 'top sellers' queries are interesting."""
    rng = rng_for(seed, "orders")
    popularity = sorted(products, key=lambda p: hash_key(f"{seed}:popularity", p["item_id"]))
    weights = [1.0 / (rank + 1) ** 0.8 for rank in range(len(popularity))]
    # Some customers order a lot, many order once or never.
    cust_weights = [rng.paretovariate(1.5) for _ in customers]
    orders, items = [], []
    for order_id in range(1, count + 1):
        cust = rng.choices(customers, weights=cust_weights)[0]
        placed = random_time(rng, max(cust["created_at"], ORDER_START), ORDER_END)
        age_days = (ORDER_END - placed).days
        if rng.random() < 0.04:
            status = "cancelled"
        elif age_days > 10:
            status = "delivered"
        elif age_days > 3:
            status = "shipped"
        else:
            status = rng.choice(["pending", "processing"])
        n_items = min(1 + int(rng.expovariate(0.9)), 6)
        chosen = {}
        while len(chosen) < n_items:
            p = rng.choices(popularity, weights=weights)[0]
            chosen[p["product_id"]] = p
        total = 0
        for p in chosen.values():
            qty = 1 if rng.random() < 0.8 else rng.randint(2, 4)
            items.append({"order_id": order_id, "product_id": p["product_id"],
                          "quantity": qty, "unit_price_cents": p["price_cents"]})
            total += qty * p["price_cents"]
        orders.append({"order_id": order_id, "customer_id": cust["customer_id"], "status": status,
                       "total_cents": total, "created_at": placed})
    orders.sort(key=lambda o: o["created_at"])
    # Renumber so order_id follows time, like a real sequence would.
    remap = {o["order_id"]: i for i, o in enumerate(orders, 1)}
    for o in orders:
        o["order_id"] = remap[o["order_id"]]
    for it in items:
        it["order_id"] = remap[it["order_id"]]
    items.sort(key=lambda it: (it["order_id"], it["product_id"]))
    return orders, items


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

SCHEMA_SQL = """\
-- Mock store schema generated by build_catalog.py from Amazon Berkeley Objects (CC BY 4.0).
-- Product names, brands, bullets, keywords and images are real ABO data;
-- prices, stock, dates, customers and orders are synthetic.

DROP TABLE IF EXISTS order_items, orders, customers, product_images, products, categories CASCADE;

CREATE TABLE categories (
    category_id   integer PRIMARY KEY,
    parent_id     integer REFERENCES categories(category_id),   -- NULL for departments
    name          text NOT NULL,
    slug          text NOT NULL UNIQUE,
    product_type  text UNIQUE                                    -- ABO product_type (leaf rows only)
);

CREATE TABLE products (
    product_id    integer PRIMARY KEY,
    item_id       text NOT NULL UNIQUE,                          -- ABO item id (ASIN-like)
    category_id   integer NOT NULL REFERENCES categories(category_id),
    name          text NOT NULL,
    brand         text NOT NULL,
    color         text,
    weight_lb     numeric(10,2),
    price_cents   integer NOT NULL CHECK (price_cents > 0),
    stock         integer NOT NULL CHECK (stock >= 0),
    description   text,                                          -- bullets joined, handy for full-text search
    bullets       jsonb NOT NULL DEFAULT '[]',
    keywords      jsonb NOT NULL DEFAULT '[]',
    node_path     text,                                          -- Amazon browse path, when present
    created_at    timestamptz NOT NULL
);
CREATE INDEX products_category_idx ON products(category_id);
CREATE INDEX products_brand_idx ON products(brand);

CREATE TABLE product_images (
    product_id    integer NOT NULL REFERENCES products(product_id) ON DELETE CASCADE,
    sort_order    integer NOT NULL,
    image_id      text NOT NULL,
    path          text NOT NULL,                                 -- relative to the bundle root
    orig_width    integer,                                       -- size of the ORIGINAL image; files are <=256px
    orig_height   integer,
    is_main       boolean NOT NULL DEFAULT false,
    PRIMARY KEY (product_id, sort_order)
);
CREATE UNIQUE INDEX product_images_one_main ON product_images(product_id) WHERE is_main;

CREATE TABLE customers (
    customer_id   integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    first_name    text NOT NULL,
    last_name     text NOT NULL,
    email         text NOT NULL UNIQUE,
    city          text,
    state         char(2),
    created_at    timestamptz NOT NULL
);

CREATE TABLE orders (
    order_id      integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    customer_id   integer NOT NULL REFERENCES customers(customer_id),
    status        text NOT NULL CHECK (status IN ('pending','processing','shipped','delivered','cancelled')),
    total_cents   integer NOT NULL CHECK (total_cents >= 0),     -- denormalized: sum of order_items
    created_at    timestamptz NOT NULL
);
CREATE INDEX orders_customer_idx ON orders(customer_id);

CREATE TABLE order_items (
    order_id         integer NOT NULL REFERENCES orders(order_id) ON DELETE CASCADE,
    product_id       integer NOT NULL REFERENCES products(product_id),
    quantity         integer NOT NULL CHECK (quantity > 0),
    unit_price_cents integer NOT NULL CHECK (unit_price_cents > 0),  -- price at time of sale
    PRIMARY KEY (order_id, product_id)
);
CREATE INDEX order_items_product_idx ON order_items(product_id);
"""

TABLE_ORDER = ["categories", "products", "product_images", "customers", "orders", "order_items"]


def iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def write_csv(path, fieldnames, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: fmt(row.get(k)) for k in fieldnames})


def fmt(v):
    if isinstance(v, bool):
        return "t" if v else "f"
    if isinstance(v, datetime):
        return iso(v)
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return v


def write_load_script(out, tables):
    lines = ["-- Run from the bundle root:  psql -d <db> -f load_postgres.sql",
             "\\set ON_ERROR_STOP on", "BEGIN;", "\\i schema.sql"]
    for t in tables:
        lines.append(f"\\copy {t} FROM 'seed/{t}.csv' WITH (FORMAT csv, HEADER true)")
    for t, col in (("customers", "customer_id"), ("orders", "order_id")):
        if t in tables:
            lines.append(f"SELECT setval(pg_get_serial_sequence('{t}', '{col}'), (SELECT max({col}) FROM {t}));")
    lines += ["COMMIT;", "ANALYZE;", ""]
    (out / "load_postgres.sql").write_text("\n".join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--abo", type=Path, default=DEFAULT_ABO, help="ABO root (has listings/ and images/)")
    ap.add_argument("--out", type=Path, default=HERE / "dist", help="output bundle directory")
    ap.add_argument("--products", type=int, default=5000, help="number of products (default 5000)")
    ap.add_argument("--seed", default="cmps-db-2026", help="seed; same seed = same dataset")
    ap.add_argument("--phone-case-share", type=float, default=0.03,
                    help="max share of CELLULAR_PHONE_CASE (default 0.03)")
    ap.add_argument("--max-type-share", type=float, default=0.06,
                    help="max share of any other single product_type (default 0.06)")
    ap.add_argument("--max-images", type=int, default=4, help="images per product, main first (default 4)")
    ap.add_argument("--names", type=Path, default=DEFAULT_NAMES, help="JSON with first_names/last_names")
    ap.add_argument("--customers", type=int, default=2000, help="synthetic customers (0 to skip)")
    ap.add_argument("--orders", type=int, default=8000, help="synthetic orders (0 to skip)")
    ap.add_argument("--no-images", action="store_true", help="skip copying image files")
    args = ap.parse_args()

    abo, out = args.abo.resolve(), args.out.resolve()
    small_dir = abo / "images/small"
    if out == abo or abo in out.parents:
        sys.exit("refusing to write inside the ABO directory")

    print(f"Reading image metadata from {abo} ...")
    images = load_images(abo)
    print(f"  {len(images):,} images")
    print("Reading listings ...")
    candidates, stats = read_candidates(abo, images, small_dir)
    for k, v in stats.items():
        print(f"  {k:20} {v:,}")

    chosen = sample(candidates, args.products, args.seed, args.phone_case_share, args.max_type_share)
    if len(chosen) < args.products:
        print(f"  note: only {len(chosen):,} products fit the caps (asked for {args.products:,})")

    # Categories: departments (parents) then product types (leaves), both in name order.
    products = [candidates[i] for i in chosen]
    for p in products:
        synth_product_fields(p, args.seed)
    departments = sorted({p["department"] for p in products})
    types = sorted({(p["department"], p["product_type"]) for p in products}, key=lambda t: (t[0], humanize(t[1])))
    categories, dept_id, type_id = [], {}, {}
    for d in departments:
        dept_id[d] = len(categories) + 1
        categories.append({"category_id": dept_id[d], "parent_id": None, "name": d,
                           "slug": slugify(d), "product_type": None})
    used_slugs = {c["slug"] for c in categories}
    for d, pt in types:
        type_id[pt] = len(categories) + 1
        slug = slugify(pt)
        if slug in used_slugs:                 # e.g. product_type FURNITURE vs department Furniture
            slug += "-other"
        used_slugs.add(slug)
        categories.append({"category_id": type_id[pt], "parent_id": dept_id[d], "name": humanize(pt),
                           "slug": slug, "product_type": pt})

    image_rows = []
    for pid, p in enumerate(products, 1):
        p["product_id"] = pid
        p["category_id"] = type_id[p["product_type"]]
        p["description"] = "\n".join(p["bullets"])
        p["images"] = build_images(p, images, small_dir, args.max_images)
        for img in p["images"]:
            image_rows.append({"product_id": pid, **img})

    customers = orders = items = []
    if args.customers and args.names.is_file():
        customers = build_customers(args.names, args.customers, args.seed)
        if args.orders:
            orders, items = build_orders(customers, products, args.orders, args.seed)
    elif args.customers:
        print(f"  names file not found ({args.names}); skipping customers and orders")

    # --- write bundle ---------------------------------------------------------
    seed_dir, mongo_dir = out / "seed", out / "mongo"
    seed_dir.mkdir(parents=True, exist_ok=True)
    mongo_dir.mkdir(parents=True, exist_ok=True)
    (out / "schema.sql").write_text(SCHEMA_SQL)

    write_csv(seed_dir / "categories.csv", ["category_id", "parent_id", "name", "slug", "product_type"], categories)
    write_csv(seed_dir / "products.csv",
              ["product_id", "item_id", "category_id", "name", "brand", "color", "weight_lb", "price_cents",
               "stock", "description", "bullets", "keywords", "node_path", "created_at"], products)
    write_csv(seed_dir / "product_images.csv",
              ["product_id", "sort_order", "image_id", "path", "orig_width", "orig_height", "is_main"], image_rows)
    tables = ["categories", "products", "product_images"]
    if customers:
        write_csv(seed_dir / "customers.csv",
                  ["customer_id", "first_name", "last_name", "email", "city", "state", "created_at"], customers)
        tables.append("customers")
    if orders:
        write_csv(seed_dir / "orders.csv", ["order_id", "customer_id", "status", "total_cents", "created_at"], orders)
        write_csv(seed_dir / "order_items.csv", ["order_id", "product_id", "quantity", "unit_price_cents"], items)
        tables += ["orders", "order_items"]
    write_load_script(out, tables)

    cat_by_type = {c["product_type"]: c for c in categories if c["product_type"]}
    with open(mongo_dir / "products.ndjson", "w", encoding="utf-8") as f:
        for p in products:
            doc = {
                "_id": p["item_id"], "product_id": p["product_id"], "name": p["name"], "brand": p["brand"],
                "category": {"department": p["department"], "name": cat_by_type[p["product_type"]]["name"],
                             "product_type": p["product_type"], "category_id": p["category_id"]},
                "price_cents": p["price_cents"], "stock": p["stock"], "color": p["color"],
                "weight_lb": p["weight_lb"], "bullets": p["bullets"], "keywords": p["keywords"],
                "node_path": p["node_path"], "attributes": p["attributes"],
                "images": [{k: img[k] for k in ("image_id", "path", "orig_width", "orig_height", "is_main")}
                           for img in p["images"]],
                "source": {"dataset": "Amazon Berkeley Objects", "domain": p["source_domain"]},
                "created_at": {"$date": iso(p["created_at"])},
            }
            f.write(json.dumps({k: v for k, v in doc.items() if v is not None}, ensure_ascii=False) + "\n")

    copied = 0
    if not args.no_images:
        for img in image_rows:
            dest = out / img["path"]
            if not dest.exists():
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(small_dir / img["src"], dest)
                copied += 1

    for name in ("LICENSE-CC-BY-4.0.txt",):
        if (abo / name).is_file():
            shutil.copy2(abo / name, out / name)
    if (HERE / "README.md").is_file():
        shutil.copy2(HERE / "README.md", out / "README.md")

    # --- summary ----------------------------------------------------------------
    print(f"\nWrote {out}")
    print(f"  categories      {len(categories):,} ({len(departments)} departments, {len(types)} product types)")
    print(f"  products        {len(products):,}")
    print(f"  product_images  {len(image_rows):,} ({copied:,} files copied this run)")
    print(f"  customers       {len(customers):,}")
    print(f"  orders          {len(orders):,}")
    print(f"  order_items     {len(items):,}")
    print("  by department:")
    for d in departments:
        n = sum(1 for p in products if p["department"] == d)
        print(f"    {d:30} {n:5,}")


if __name__ == "__main__":
    main()
