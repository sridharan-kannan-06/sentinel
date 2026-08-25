"""Render the injection fixture as a PDF.

The attack does not need a PDF to work; Model Armor screens the extracted text
either way. The PDF exists so that the demonstration shows a document arriving
rather than a string being posted, which is what it would look like in a hospital.

Writes the PDF by hand rather than pulling in a rendering library. A fixture
generator that needs a dependency is a fixture generator that stops working.

    python fixtures/make_injection_pdf.py
"""

from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "injection_claim.txt"
TARGET = HERE / "injection_claim.pdf"

PAGE_WIDTH, PAGE_HEIGHT = 595, 842  # A4 in points
LEFT_MARGIN, TOP_MARGIN = 56, 786
LINE_HEIGHT = 13.5
FONT_SIZE = 10


def escape(text: str) -> str:
    """PDF strings are parenthesised, so those three characters need escaping."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def build_content(lines: list[str]) -> str:
    parts = ["BT", f"/F1 {FONT_SIZE} Tf", f"{LINE_HEIGHT} TL", f"1 0 0 1 {LEFT_MARGIN} {TOP_MARGIN} Tm"]
    for line in lines:
        parts.append(f"({escape(line)}) Tj")
        parts.append("T*")
    parts.append("ET")
    return "\n".join(parts)


def build_pdf(content: str) -> bytes:
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            "/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>"
        ),
        None,  # the content stream, built below
        "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>",
    ]

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []

    for index, body in enumerate(objects, start=1):
        offsets.append(len(out))
        if body is None:
            encoded = content.encode("latin-1", "replace")
            out += f"{index} 0 obj\n<< /Length {len(encoded)} >>\nstream\n".encode("latin-1")
            out += encoded
            out += b"\nendstream\nendobj\n"
        else:
            out += f"{index} 0 obj\n{body}\nendobj\n".encode("latin-1")

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode("latin-1")
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode("latin-1")
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_at}\n%%EOF\n"
    ).encode("latin-1")
    return bytes(out)


def main() -> int:
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    # Courier at ten point fits roughly this many characters across A4.
    wrapped: list[str] = []
    for line in lines:
        while len(line) > 78:
            cut = line.rfind(" ", 0, 78)
            cut = cut if cut > 0 else 78
            wrapped.append(line[:cut])
            line = line[cut:].lstrip()
        wrapped.append(line)

    TARGET.write_bytes(build_pdf(build_content(wrapped)))
    print(f"Wrote {TARGET} ({TARGET.stat().st_size} bytes, {len(wrapped)} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
