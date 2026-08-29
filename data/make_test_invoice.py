"""
data/make_test_invoice.py
Generates a synthetic supplier invoice PNG for testing multimodal extraction.
Run: python3 data/make_test_invoice.py
"""
from PIL import Image, ImageDraw
import io

def make_invoice() -> bytes:
    img  = Image.new("RGB", (640, 420), color="white")
    draw = ImageDraw.Draw(img)

    # Header
    draw.rectangle([0, 0, 640, 65], fill="#1a1a2e")
    draw.text((20, 12), "SPORTIFY SUPPLIES PVT LTD", fill="white")
    draw.text((20, 38), "Supplier Invoice #INV-2026-0847", fill="#cccccc")

    draw.text((20, 80),  "Bill To: Kaya Sportswear", fill="black")
    draw.text((20, 100), "Date: 29 August 2026",     fill="black")

    # Table header
    draw.rectangle([20, 128, 620, 152], fill="#f0f0f0")
    for x, label in [(25,"SKU"),(140,"Product"),(340,"Qty"),(400,"Unit Cost (Rs)"),(530,"Total")]:
        draw.text((x, 135), label, fill="#333333")

    items = [
        ("SHOE-001", "Running Shoes Size 8",     "50",  "620.00",  "31,000"),
        ("SOCK-3PK", "Performance Socks 3-Pack", "200", "210.00",  "42,000"),
        ("INSOLE-1", "Gel Insoles",              "100", "149.00",  "14,900"),
        ("BOTTLE-1", "750ml Water Bottle",       "75",  "359.00",  "26,925"),
        ("CAP-1",    "Running Cap",              "120", "239.00",  "28,680"),
    ]

    y = 162
    for sku, product, qty, unit, total in items:
        draw.text((25,  y), sku,     fill="black")
        draw.text((140, y), product, fill="black")
        draw.text((340, y), qty,     fill="black")
        draw.text((400, y), f"Rs. {unit}", fill="#2d6a4f")
        draw.text((530, y), total,   fill="black")
        draw.line([(20, y+18), (620, y+18)], fill="#eeeeee", width=1)
        y += 26

    # Footer
    draw.rectangle([0, 370, 640, 420], fill="#f8f9fa")
    draw.text((20, 378), "Prices in INR.  Payment: 30 days net.", fill="#666666")
    draw.text((20, 396), "These are supplier costs (COGS), not retail prices.", fill="#888888")

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()

if __name__ == "__main__":
    data = make_invoice()
    with open("data/test_invoice.png", "wb") as f:
        f.write(data)
    print(f"Saved data/test_invoice.png ({len(data):,} bytes)")
