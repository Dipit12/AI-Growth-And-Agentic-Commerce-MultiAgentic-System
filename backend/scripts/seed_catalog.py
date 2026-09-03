"""Populate ~50 synthetic products across 5 categories. Layer 4 (integrations & data) helper script.

Idempotent by `sku`: re-running skips rows that already exist. Does not generate embeddings —
see scripts/index_catalog.py for that step.

Run with: python -m scripts.seed_catalog
"""

import asyncio

from sqlalchemy import select

from app.db.session import async_session_factory
from app.logging_config import configure_logging, get_logger
from app.models.product import Product

logger = get_logger("seed_catalog")

PRODUCTS: list[dict] = [
    # electronics
    {"sku": "ELEC-001", "name": "Wireless Mechanical Keyboard", "description": "A hot-swappable mechanical keyboard with per-key RGB and a 2.4GHz wireless dongle. Tactile switches rated for 50 million keystrokes.", "price_paise": 349900, "stock": 42, "category": "electronics", "attributes": {"brand": "KeyForge", "color": "black"}},
    {"sku": "ELEC-002", "name": "Wireless Mouse", "description": "An ergonomic wireless mouse with a silent-click design and adjustable DPI up to 4000. Pairs over Bluetooth or the included USB receiver.", "price_paise": 129900, "stock": 88, "category": "electronics", "attributes": {"brand": "KeyForge", "color": "graphite"}},
    {"sku": "ELEC-003", "name": "USB-C Hub 7-in-1", "description": "A compact aluminium hub adding HDMI, two USB-A ports, an SD card reader, and 100W pass-through charging to any USB-C laptop.", "price_paise": 219900, "stock": 60, "category": "electronics", "attributes": {"brand": "PortLink", "ports": 7}},
    {"sku": "ELEC-004", "name": "Bluetooth Earbuds", "description": "True wireless earbuds with active noise cancellation and 30-hour total battery life via the charging case. IPX4 sweat resistant.", "price_paise": 249900, "stock": 75, "category": "electronics", "attributes": {"brand": "SoundLoop", "anc": True}},
    {"sku": "ELEC-005", "name": "Portable SSD 1TB", "description": "A pocket-sized 1TB solid-state drive with USB 3.2 Gen 2 transfer speeds up to 1050MB/s and a shock-resistant aluminium shell.", "price_paise": 649900, "stock": 34, "category": "electronics", "attributes": {"brand": "DataVault", "capacity_gb": 1000}},
    {"sku": "ELEC-006", "name": "27-inch 4K Monitor", "description": "A 27-inch IPS panel at 4K resolution with 99% sRGB coverage and a 60Hz refresh rate, ideal for design and productivity work.", "price_paise": 2299900, "stock": 18, "category": "electronics", "attributes": {"brand": "ViewCrate", "size_inch": 27}},
    {"sku": "ELEC-007", "name": "Webcam 1080p", "description": "A 1080p USB webcam with autofocus and a built-in dual-microphone array, plus a privacy shutter for video calls.", "price_paise": 249900, "stock": 55, "category": "electronics", "attributes": {"brand": "ViewCrate", "resolution": "1080p"}},
    {"sku": "ELEC-008", "name": "Power Bank 20000mAh", "description": "A high-capacity 20000mAh power bank with 22.5W fast charging and dual USB-A plus one USB-C output for charging three devices at once.", "price_paise": 189900, "stock": 96, "category": "electronics", "attributes": {"brand": "ChargeCell", "capacity_mah": 20000}},
    {"sku": "ELEC-009", "name": "Smart LED Desk Lamp", "description": "A dimmable LED desk lamp with adjustable colour temperature, a built-in USB charging port, and touch controls.", "price_paise": 149900, "stock": 70, "category": "electronics", "attributes": {"brand": "GlowDesk", "dimmable": True}},
    {"sku": "ELEC-010", "name": "Noise Cancelling Headphones", "description": "Over-ear wireless headphones with adaptive active noise cancellation and 40 hours of battery life on a single charge.", "price_paise": 799900, "stock": 27, "category": "electronics", "attributes": {"brand": "SoundLoop", "anc": True}},
    # kitchen
    {"sku": "KIT-001", "name": "Stainless Steel Pressure Cooker 5L", "description": "A 5-litre induction-compatible pressure cooker with a triple safety valve and a mirror-finish stainless steel body.", "price_paise": 189900, "stock": 40, "category": "kitchen", "attributes": {"brand": "HearthPro", "capacity_l": 5}},
    {"sku": "KIT-002", "name": "Non-stick Frying Pan 28cm", "description": "A 28cm non-stick frying pan with a reinforced ceramic coating and a stay-cool riveted handle, safe for all stovetops.", "price_paise": 99900, "stock": 65, "category": "kitchen", "attributes": {"brand": "HearthPro", "diameter_cm": 28}},
    {"sku": "KIT-003", "name": "Electric Kettle 1.5L", "description": "A 1.5-litre electric kettle with a rapid-boil element, auto shut-off, and a concealed heating coil for easy cleaning.", "price_paise": 129900, "stock": 58, "category": "kitchen", "attributes": {"brand": "BrewFast", "capacity_l": 1.5}},
    {"sku": "KIT-004", "name": "Mixer Grinder 750W", "description": "A 750W mixer grinder with three stainless steel jars for wet grinding, dry grinding, and chutney making.", "price_paise": 249900, "stock": 33, "category": "kitchen", "attributes": {"brand": "HearthPro", "watts": 750}},
    {"sku": "KIT-005", "name": "Air Fryer 4L", "description": "A 4-litre digital air fryer with 8 preset cooking modes and a non-stick basket, using up to 85% less oil than deep frying.", "price_paise": 449900, "stock": 29, "category": "kitchen", "attributes": {"brand": "CrispWell", "capacity_l": 4}},
    {"sku": "KIT-006", "name": "Chef's Knife Set", "description": "A 5-piece forged stainless steel knife set including a chef's knife, santoku, and paring knife, with a magnetic block.", "price_paise": 179900, "stock": 45, "category": "kitchen", "attributes": {"brand": "SharpEdge", "pieces": 5}},
    {"sku": "KIT-007", "name": "Glass Storage Container Set (10pc)", "description": "A 10-piece borosilicate glass storage set with airtight snap lids, safe for the fridge, microwave, and oven.", "price_paise": 89900, "stock": 80, "category": "kitchen", "attributes": {"brand": "PurePack", "pieces": 10}},
    {"sku": "KIT-008", "name": "French Press Coffee Maker", "description": "A 600ml borosilicate glass French press with a stainless steel mesh filter for full-bodied, sediment-free coffee.", "price_paise": 79900, "stock": 52, "category": "kitchen", "attributes": {"brand": "BrewFast", "capacity_ml": 600}},
    {"sku": "KIT-009", "name": "Induction Cooktop 2000W", "description": "A 2000W induction cooktop with 8 power levels, a touch-sensitive control panel, and automatic pan-detection.", "price_paise": 219900, "stock": 37, "category": "kitchen", "attributes": {"brand": "HearthPro", "watts": 2000}},
    {"sku": "KIT-010", "name": "Bamboo Cutting Board Set", "description": "A 3-piece bamboo cutting board set in graduated sizes, naturally antimicrobial and gentle on knife edges.", "price_paise": 59900, "stock": 90, "category": "kitchen", "attributes": {"brand": "EcoCarve", "pieces": 3}},
    # stationery
    {"sku": "STA-001", "name": "Leather Bound Notebook A5", "description": "An A5 notebook with a genuine leather cover, 200 pages of dotted acid-free paper, and an elastic closure band.", "price_paise": 39900, "stock": 110, "category": "stationery", "attributes": {"brand": "InkLeaf", "size": "A5"}},
    {"sku": "STA-002", "name": "Gel Pen Set (10 colors)", "description": "A set of 10 quick-drying gel pens in vibrant colours with 0.5mm tips, smudge-resistant on most paper stocks.", "price_paise": 24900, "stock": 150, "category": "stationery", "attributes": {"brand": "ColorFlow", "pieces": 10}},
    {"sku": "STA-003", "name": "Mechanical Pencil Set", "description": "A set of 3 mechanical pencils with 0.5mm lead, built-in erasers, and a comfortable rubberised grip.", "price_paise": 34900, "stock": 95, "category": "stationery", "attributes": {"brand": "InkLeaf", "pieces": 3}},
    {"sku": "STA-004", "name": "Desk Organizer Bamboo", "description": "A multi-compartment bamboo desk organizer for pens, sticky notes, and a phone stand, keeping a workspace tidy.", "price_paise": 79900, "stock": 60, "category": "stationery", "attributes": {"brand": "EcoCarve", "material": "bamboo"}},
    {"sku": "STA-005", "name": "Sticky Notes Pack (12 pads)", "description": "A pack of 12 sticky note pads in assorted pastel colours, 100 sheets each, with strong yet residue-free adhesive.", "price_paise": 19900, "stock": 200, "category": "stationery", "attributes": {"brand": "ColorFlow", "pads": 12}},
    {"sku": "STA-006", "name": "Fountain Pen Classic", "description": "A classic fountain pen with a stainless steel nib, converter-refillable ink chamber, and a brass-weighted barrel.", "price_paise": 149900, "stock": 40, "category": "stationery", "attributes": {"brand": "InkLeaf", "nib": "medium"}},
    {"sku": "STA-007", "name": "Whiteboard Magnetic 2x3ft", "description": "A 2x3 foot magnetic dry-erase whiteboard with an aluminium frame and a mounting kit for wall installation.", "price_paise": 129900, "stock": 25, "category": "stationery", "attributes": {"brand": "PlanBoard", "size_ft": "2x3"}},
    {"sku": "STA-008", "name": "Highlighter Set (6 colors)", "description": "A set of 6 chisel-tip highlighters in fluorescent colours, low odour and fast-drying to avoid smearing on notes.", "price_paise": 14900, "stock": 180, "category": "stationery", "attributes": {"brand": "ColorFlow", "pieces": 6}},
    {"sku": "STA-009", "name": "Planner Diary 2026", "description": "A dated 2026 planner diary with weekly and monthly spreads, a ribbon bookmark, and a slot for a pen loop.", "price_paise": 49900, "stock": 70, "category": "stationery", "attributes": {"brand": "PlanBoard", "year": 2026}},
    {"sku": "STA-010", "name": "Calculator Scientific", "description": "A scientific calculator with 240 functions, a two-line natural textbook display, and solar-assisted power.", "price_paise": 69900, "stock": 55, "category": "stationery", "attributes": {"brand": "NumCraft", "functions": 240}},
    # fitness
    {"sku": "FIT-001", "name": "Yoga Mat 6mm", "description": "A 6mm thick non-slip yoga mat made from TPE foam, lightweight and moisture-resistant with a carry strap included.", "price_paise": 99900, "stock": 85, "category": "fitness", "attributes": {"brand": "FlexCore", "thickness_mm": 6}},
    {"sku": "FIT-002", "name": "Adjustable Dumbbell Set 20kg", "description": "A pair of adjustable dumbbells totalling 20kg, with quick-change weight plates and a compact storage tray.", "price_paise": 349900, "stock": 22, "category": "fitness", "attributes": {"brand": "IronForm", "total_kg": 20}},
    {"sku": "FIT-003", "name": "Resistance Bands Set (5pc)", "description": "A set of 5 latex resistance bands in increasing tension levels, with door anchor and handles for full-body workouts.", "price_paise": 59900, "stock": 100, "category": "fitness", "attributes": {"brand": "FlexCore", "pieces": 5}},
    {"sku": "FIT-004", "name": "Foam Roller", "description": "A high-density foam roller for myofascial release and post-workout muscle recovery, textured for deep-tissue massage.", "price_paise": 79900, "stock": 64, "category": "fitness", "attributes": {"brand": "FlexCore", "length_cm": 45}},
    {"sku": "FIT-005", "name": "Skipping Rope Speed", "description": "A ball-bearing speed skipping rope with a lightweight steel cable and an adjustable length for cardio training.", "price_paise": 29900, "stock": 130, "category": "fitness", "attributes": {"brand": "CardioMax", "adjustable": True}},
    {"sku": "FIT-006", "name": "Fitness Tracker Watch", "description": "A fitness tracker with heart-rate monitoring, sleep tracking, and 15 sport modes, with a 7-day battery life.", "price_paise": 299900, "stock": 48, "category": "fitness", "attributes": {"brand": "PulseTrack", "battery_days": 7}},
    {"sku": "FIT-007", "name": "Protein Shaker Bottle", "description": "A 700ml leak-proof shaker bottle with a stainless steel wire whisk ball for smooth, lump-free protein shakes.", "price_paise": 24900, "stock": 160, "category": "fitness", "attributes": {"brand": "CardioMax", "capacity_ml": 700}},
    {"sku": "FIT-008", "name": "Gym Gloves Pair", "description": "A pair of padded gym gloves with breathable mesh backing and wrist wraps for extra support during lifting.", "price_paise": 44900, "stock": 90, "category": "fitness", "attributes": {"brand": "IronForm", "size": "M/L"}},
    {"sku": "FIT-009", "name": "Ab Wheel Roller", "description": "A dual-wheel ab roller with a non-slip grip and an integrated knee pad for stable core-strengthening workouts.", "price_paise": 54900, "stock": 70, "category": "fitness", "attributes": {"brand": "IronForm", "wheels": 2}},
    {"sku": "FIT-010", "name": "Pull-up Bar Doorway", "description": "A doorway pull-up bar rated for 100kg, installs without drilling and adjusts to fit most standard door frames.", "price_paise": 129900, "stock": 35, "category": "fitness", "attributes": {"brand": "IronForm", "max_kg": 100}},
    # home
    {"sku": "HOM-001", "name": "Memory Foam Pillow", "description": "A contoured memory foam pillow with a cooling gel layer and a removable, machine-washable bamboo cover.", "price_paise": 89900, "stock": 75, "category": "home", "attributes": {"brand": "RestWell", "material": "memory foam"}},
    {"sku": "HOM-002", "name": "Cotton Bedsheet Set Queen", "description": "A queen-size 100% cotton bedsheet set with 2 pillow covers, 300 thread count, and a deep-fit elastic corner.", "price_paise": 179900, "stock": 50, "category": "home", "attributes": {"brand": "RestWell", "size": "queen"}},
    {"sku": "HOM-003", "name": "LED String Lights 10m", "description": "A 10-metre warm-white LED string light with 8 lighting modes, remote control, and IP44 weather resistance.", "price_paise": 49900, "stock": 120, "category": "home", "attributes": {"brand": "GlowDesk", "length_m": 10}},
    {"sku": "HOM-004", "name": "Aroma Diffuser Ultrasonic", "description": "An ultrasonic aroma diffuser with a 300ml tank, 7-colour LED mood lighting, and whisper-quiet operation.", "price_paise": 119900, "stock": 55, "category": "home", "attributes": {"brand": "CalmAir", "capacity_ml": 300}},
    {"sku": "HOM-005", "name": "Wall Clock Minimalist", "description": "A 12-inch minimalist wall clock with a silent sweep movement and a matte-finish aluminium frame.", "price_paise": 69900, "stock": 65, "category": "home", "attributes": {"brand": "TimeFrame", "size_inch": 12}},
    {"sku": "HOM-006", "name": "Storage Ottoman", "description": "A cube storage ottoman with a hinged lid, faux-leather upholstery, and a hidden compartment for blankets or toys.", "price_paise": 249900, "stock": 20, "category": "home", "attributes": {"brand": "CozyNest", "material": "faux leather"}},
    {"sku": "HOM-007", "name": "Curtain Set Blackout 2pc", "description": "A set of 2 blackout curtain panels that block 99% of outside light, with a rod-pocket header for easy hanging.", "price_paise": 149900, "stock": 45, "category": "home", "attributes": {"brand": "CozyNest", "pieces": 2}},
    {"sku": "HOM-008", "name": "Door Mat Coir", "description": "A natural coir door mat with a non-slip PVC backing and a durable herringbone weave that scrapes shoes clean.", "price_paise": 39900, "stock": 100, "category": "home", "attributes": {"brand": "EcoCarve", "material": "coir"}},
    {"sku": "HOM-009", "name": "Table Lamp Ceramic", "description": "A ceramic table lamp with a linen drum shade and a warm-white LED bulb included, ideal for a bedside table.", "price_paise": 99900, "stock": 40, "category": "home", "attributes": {"brand": "GlowDesk", "material": "ceramic"}},
    {"sku": "HOM-010", "name": "Photo Frame Collage Set", "description": "A set of 8 wooden photo frames in assorted sizes for a gallery-wall collage, pre-drilled with hanging hardware.", "price_paise": 59900, "stock": 60, "category": "home", "attributes": {"brand": "TimeFrame", "pieces": 8}},
]


async def seed() -> None:
    async with async_session_factory() as session:
        existing_skus = set(
            (await session.execute(select(Product.sku))).scalars().all()
        )
        new_products = [
            Product(**data) for data in PRODUCTS if data["sku"] not in existing_skus
        ]
        if not new_products:
            logger.info("seed_catalog.skip", reason="all skus already present", count=len(PRODUCTS))
            return

        session.add_all(new_products)
        await session.commit()
        logger.info(
            "seed_catalog.inserted",
            inserted=len(new_products),
            skipped=len(PRODUCTS) - len(new_products),
        )


if __name__ == "__main__":
    configure_logging()
    asyncio.run(seed())
