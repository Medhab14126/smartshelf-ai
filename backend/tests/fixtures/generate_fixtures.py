"""Generate synthetic sample invoices and packaging labels.

All names, numbers and dates are fictional. Run:

    python tests/fixtures/generate_fixtures.py

Outputs into this directory (and the repository's /samples folder).
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
SAMPLES = HERE.parents[2] / "samples"
FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    try:
        return ImageFont.truetype(str(FONT_DIR / name), size)
    except OSError:
        return ImageFont.load_default(size)


# --- EAN-13 rendering ---------------------------------------------------------
_L = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
_G = ["0100111", "0110011", "0011011", "0100001", "0011101", "0111001", "0000101", "0010001", "0001001", "0010111"]
_R = ["1110010", "1100110", "1101100", "1000010", "1011100", "1001110", "1010000", "1000100", "1001000", "1110100"]
_PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def ean13_bits(code: str) -> str:
    digits = [int(c) for c in code]
    bits = "101"
    for i, d in enumerate(digits[1:7]):
        bits += (_L if _PARITY[digits[0]][i] == "L" else _G)[d]
    bits += "01010"
    for d in digits[7:]:
        bits += _R[d]
    return bits + "101"


def draw_ean13(draw: ImageDraw.ImageDraw, code: str, x: int, y: int, module: int = 4, height: int = 160) -> None:
    for i, bit in enumerate(ean13_bits(code)):
        if bit == "1":
            draw.rectangle([x + i * module, y, x + (i + 1) * module - 1, y + height], fill="black")
    draw.text((x, y + height + 6), code, fill="black", font=font(28))


# --- Invoices -----------------------------------------------------------------
DAIRY_ITEMS = [
    ("Toned Milk 500ml", 20, "packet", 30.00),
    ("Paneer 200g", 5, "pcs", 90.00),
    ("Fresh Cream 1L", 4, "bottle", 210.00),
    ("Greek Yogurt 400g", 6, "tub", 125.50),
]


def make_dairy_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    y = height - 60
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, y, "GreenLeaf Dairy Supplies")
    c.setFont("Helvetica", 10)
    c.drawString(50, y - 16, "12 Market Road, Bengaluru 560001")
    c.setFont("Helvetica-Bold", 13)
    c.drawRightString(width - 50, y, "TAX INVOICE")
    c.setFont("Helvetica", 10)
    c.drawRightString(width - 50, y - 16, "Invoice No: GLD-2026-0457")
    c.drawRightString(width - 50, y - 30, "Invoice Date: 27/10/2026")
    c.drawString(50, y - 50, "Bill To: Corner Cup Cafe")
    y -= 90
    cols = [50, 80, 300, 350, 420, 500]
    c.setFont("Helvetica-Bold", 10)
    for x, title in zip(cols, ["S.No", "Description", "Qty", "Unit", "Rate", "Amount"], strict=True):
        c.drawString(x, y, title)
    c.setFont("Helvetica", 10)
    subtotal = 0.0
    for i, (name, qty, unit, rate) in enumerate(DAIRY_ITEMS, start=1):
        y -= 18
        amount = qty * rate
        subtotal += amount
        for x, value in zip(cols, [str(i), name, str(qty), unit, f"{rate:.2f}", f"{amount:,.2f}"], strict=True):
            c.drawString(x, y, value)
    tax = round(subtotal * 0.025, 2)
    y -= 36
    c.drawString(350, y, "Subtotal")
    c.drawRightString(width - 50, y, f"{subtotal:,.2f}")
    c.drawString(350, y - 16, "CGST @ 2.5%")
    c.drawRightString(width - 50, y - 16, f"{tax:,.2f}")
    c.drawString(350, y - 32, "SGST @ 2.5%")
    c.drawRightString(width - 50, y - 32, f"{tax:,.2f}")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(350, y - 52, "Grand Total")
    c.drawRightString(width - 50, y - 52, f"Rs. {subtotal + 2 * tax:,.2f}")
    c.showPage()
    c.save()


def make_multipage_pdf(path: Path) -> None:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    items = [(f"Coffee Cup Lids Pack {i}", 2, "box", 150.0 + i) for i in range(1, 31)]
    c = canvas.Canvas(str(path), pagesize=A4)
    width, height = A4
    subtotal = 0.0
    for page in range(2):
        y = height - 60
        c.setFont("Helvetica-Bold", 14)
        c.drawString(50, y, "CafeWare Packaging Co")
        c.setFont("Helvetica", 10)
        c.drawRightString(width - 50, y, "Invoice No: CW-55120")
        c.drawRightString(width - 50, y - 14, "Date: 2026-10-24")
        c.drawRightString(width - 50, y - 28, f"Page {page + 1} of 2")
        y -= 60
        c.setFont("Helvetica-Bold", 10)
        for x, title in zip([50, 300, 360, 420, 500], ["Item", "Qty", "Unit", "Price", "Amount"], strict=True):
            c.drawString(x, y, title)
        c.setFont("Helvetica", 10)
        for name, qty, unit, price in items[page * 15 : (page + 1) * 15]:
            y -= 18
            subtotal += qty * price
            for x, value in zip(
                [50, 300, 360, 420, 500], [name, str(qty), unit, f"{price:.2f}", f"{qty * price:.2f}"], strict=True
            ):
                c.drawString(x, y, value)
        if page == 1:
            c.setFont("Helvetica-Bold", 11)
            c.drawString(360, y - 40, "Total Amount")
            c.drawRightString(width - 50, y - 40, f"INR {subtotal:,.2f}")
        c.showPage()
    c.save()


def make_bakery_png(path: Path) -> None:
    img = Image.new("RGB", (1700, 1300), "white")
    d = ImageDraw.Draw(img)
    d.text((80, 60), "FRESH BAKE CO.", fill="black", font=font(56, bold=True))
    d.text((80, 140), "88 Baker Street, Pune", fill="black", font=font(30))
    d.text((1050, 70), "INVOICE #FB-88213", fill="black", font=font(36, bold=True))
    d.text((1050, 130), "Invoice Date: October 26, 2026", fill="black", font=font(30))
    y = 280
    cols = [80, 330, 1000, 1150, 1400]
    for x, title in zip(cols, ["Item Code", "Item", "Qty", "Price", "Total"], strict=True):
        d.text((x, y), title, fill="black", font=font(32, bold=True))
    rows = [
        ("BR-001", "Sourdough Loaf", "10", "45.00", "450.00"),
        ("BR-014", "Butter Croissant", "24", "35.00", "840.00"),
        ("FL-220", "Multigrain Bun", "30", "12.50", "375.00"),
    ]
    for row in rows:
        y += 80
        for x, value in zip(cols, row, strict=True):
            d.text((x, y), value, fill="black", font=font(32))
    y += 140
    for label, value in (("Subtotal", "1,665.00"), ("Tax", "83.25"), ("Total", "1,748.25")):
        d.text((1000, y), label, fill="black", font=font(32, bold=label == "Total"))
        d.text((1400, y), value, fill="black", font=font(32, bold=label == "Total"))
        y += 60
    d.text((80, y + 40), "All amounts in INR", fill="black", font=font(28))
    img.save(path)


# --- Packaging labels ---------------------------------------------------------


def make_label(path: Path, title: str, brand: str, lines: list[str], barcode: str | None = None) -> None:
    img = Image.new("RGB", (1400, 1000), (250, 248, 240))
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 1380, 980], outline="black", width=4)
    d.text((70, 60), brand, fill="black", font=font(40))
    d.text((70, 120), title, fill="black", font=font(96, bold=True))
    y = 290
    for line in lines:
        d.text((70, y), line, fill="black", font=font(44))
        y += 70
    if barcode:
        draw_ean13(d, barcode, 850, 640, module=5, height=220)
    img.save(path)


def main() -> None:
    HERE.mkdir(exist_ok=True)
    make_dairy_pdf(HERE / "invoice_dairy.pdf")
    make_multipage_pdf(HERE / "invoice_multipage.pdf")
    make_bakery_png(HERE / "invoice_bakery.png")
    make_label(
        HERE / "label_milk.png",
        "Toned Milk",
        "Brand: GreenLeaf",
        ["Net Qty: 500 ml", "MFG: 25/10/2026", "EXP: 01/11/2026", "Batch No: A123"],
        barcode="8901234567890",
    )
    make_label(
        HERE / "label_ambiguous.png",
        "Fresh Cream",
        "Brand: Dairy Gold",
        ["Net Qty: 1 L", "PKD ON 03/04/2027", "USE BY 05/04/2027", "Lot: L2231"],
    )
    make_label(
        HERE / "label_bestbefore.png",
        "Choco Cookies",
        "Brand: Crumbly",
        ["Net Wt: 200 g", "Mfd. 12-Sep-2026", "Best Before 12 Mar 2027", "Batch: CK0912"],
    )
    SAMPLES.mkdir(exist_ok=True)
    for file in HERE.iterdir():
        if file.suffix in {".pdf", ".png"}:
            shutil.copy(file, SAMPLES / file.name)
    print("fixtures written to", HERE, "and", SAMPLES)


if __name__ == "__main__":
    main()
