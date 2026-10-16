"""Write the document fixtures the suites extract from.

Binary fixtures that cannot be read in a diff have to be regenerable, otherwise nobody
can tell later what a test is actually asserting against. Run after changing anything
the assertions in tests/kernel-api.mjs pin:

    .venv/Scripts/python.exe scripts/make_doc_fixtures.py

The same reading table goes into the docx, the pptx and the pdf, so the suite can assert
that one table profiles alike whatever carried it, the way fixture_measurements.xlsx and
fixture_measurements.csv do for a sheet and a CSV.

python-docx, python-pptx and pymupdf write the three formats. The mislabelled fixtures
are byte copies under a suffix that lies, which is what the format sniffing is for;
the corrupt ones are truncations.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
from docx import Document
from pptx import Presentation
from pptx.util import Inches

CONTENTS = Path(__file__).resolve().parents[1] / "contents"

TITLE = "Measurement protocol"
METHOD = (
    "Each sensor is read once per day at 08:00 local time. A reading is recorded in "
    "degrees Celsius and is never corrected after the fact."
)
DICTIONARY = (
    "sensor_id identifies the sensor in the basin. value is the reading. unit is the "
    "unit of the reading and is always degC."
)

READINGS = [
    ["sensor_id", "value", "unit"],
    ["S-11", "21.5", "degC"],
    ["S-12", "22", "degC"],
    ["S-13", "20.25", "degC"],
]

TERMS = [
    ["term", "meaning"],
    ["sensor_id", "identifier of the sensor"],
    ["value", "the reading"],
    ["unit", "unit of the reading"],
]

# A pdf table is reconstructed from glyph positions, and an underscore sits far enough
# below the other characters that the extractor reads it as a second line: "sensor_id"
# comes back as "sensor id\n_". The pdf fixture spells the column with a space so the
# assertions pin extraction rather than that artifact.
PDF_READINGS = [["sensor id" if cell == "sensor_id" else cell for cell in row] for row in READINGS]


def write_docx_table(document, rows: list[list[str]]) -> None:
    table = document.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Table Grid"
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            table.cell(row_index, column_index).text = value


def protocol_docx(path: Path) -> None:
    """Two tables under two headings, which is what a protocol with a data dictionary is."""
    document = Document()
    document.add_paragraph(TITLE, style="Title")
    document.add_paragraph("Method", style="Heading 1")
    document.add_paragraph(METHOD)
    document.add_paragraph("Readings", style="Heading 1")
    write_docx_table(document, READINGS)
    document.add_paragraph("Data dictionary", style="Heading 1")
    document.add_paragraph(DICTIONARY)
    write_docx_table(document, TERMS)
    document.save(path)


def notes_docx(path: Path) -> None:
    document = Document()
    document.add_paragraph("Field notes", style="Title")
    document.add_paragraph(METHOD)
    document.add_paragraph(DICTIONARY)
    document.save(path)


def overview_pptx(path: Path) -> None:
    presentation = Presentation()
    title_slide = presentation.slides.add_slide(presentation.slide_layouts[0])
    title_slide.shapes.title.text = "Sensor overview"
    title_slide.placeholders[1].text = "Basin monitoring 2024"

    method_slide = presentation.slides.add_slide(presentation.slide_layouts[1])
    method_slide.shapes.title.text = "Method"
    body = method_slide.placeholders[1].text_frame
    body.text = "One reading per sensor per day"
    body.add_paragraph().text = "Values in degC"

    table_slide = presentation.slides.add_slide(presentation.slide_layouts[5])
    table_slide.shapes.title.text = "Readings"
    shape = table_slide.shapes.add_table(
        len(READINGS), len(READINGS[0]), Inches(1), Inches(2), Inches(6), Inches(2)
    )
    for row_index, row in enumerate(READINGS):
        for column_index, value in enumerate(row):
            shape.table.cell(row_index, column_index).text = value
    presentation.save(path)


def plain_pptx(path: Path) -> None:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.shapes.title.text = "Field notes"
    first.placeholders[1].text = "No tables here"

    second = presentation.slides.add_slide(presentation.slide_layouts[1])
    second.shapes.title.text = "Method"
    second.placeholders[1].text_frame.text = METHOD
    presentation.save(path)


def draw_table(page, rows: list[list[str]], top: float) -> None:
    """A ruled grid, because find_tables locates a table by its lines first."""
    left, column_width, row_height = 60.0, 140.0, 24.0
    width = column_width * len(rows[0])
    height = row_height * len(rows)
    for index in range(len(rows) + 1):
        y = top + index * row_height
        page.draw_line(pymupdf.Point(left, y), pymupdf.Point(left + width, y))
    for index in range(len(rows[0]) + 1):
        x = left + index * column_width
        page.draw_line(pymupdf.Point(x, top), pymupdf.Point(x, top + height))
    for row_index, row in enumerate(rows):
        for column_index, value in enumerate(row):
            page.insert_text(
                pymupdf.Point(left + column_index * column_width + 6, top + row_index * row_height + 16),
                value,
                fontsize=11,
            )


def report_pdf(path: Path) -> None:
    """Text on page one, the table on page two, so a locator has something to name."""
    document = pymupdf.open()
    first = document.new_page()
    first.insert_text(pymupdf.Point(60, 72), TITLE, fontsize=18)
    first.insert_textbox(pymupdf.Rect(60, 100, 540, 220), METHOD, fontsize=11)
    first.insert_textbox(pymupdf.Rect(60, 230, 540, 350), DICTIONARY, fontsize=11)

    second = document.new_page()
    second.insert_text(pymupdf.Point(60, 72), "Readings", fontsize=14)
    draw_table(second, PDF_READINGS, 100.0)
    document.set_toc([[1, TITLE, 1], [1, "Readings", 2]])
    document.save(path)
    document.close()


def notes_pdf(path: Path) -> None:
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text(pymupdf.Point(60, 72), "Field notes", fontsize=18)
    page.insert_textbox(pymupdf.Rect(60, 100, 540, 220), METHOD, fontsize=11)
    document.save(path)
    document.close()


def truncate(path: Path, source: Path, keep: int) -> None:
    path.write_bytes(source.read_bytes()[:keep])


def main() -> None:
    protocol = CONTENTS / "fixture_protocol.docx"
    overview = CONTENTS / "fixture_overview.pptx"
    report = CONTENTS / "fixture_report.pdf"

    protocol_docx(protocol)
    notes_docx(CONTENTS / "fixture_notes.docx")
    overview_pptx(overview)
    plain_pptx(CONTENTS / "fixture_plain.pptx")
    report_pdf(report)
    notes_pdf(CONTENTS / "fixture_notes.pdf")

    # A zip keeps its central directory at the end, so a third of one no longer opens.
    # MuPDF rebuilds a damaged cross-reference table instead: a third of the pdf still
    # yields two pages, so only the header line survives here.
    truncate(CONTENTS / "fixture_corrupt.docx", protocol, protocol.stat().st_size // 3)
    truncate(CONTENTS / "fixture_corrupt.pptx", overview, overview.stat().st_size // 3)
    truncate(CONTENTS / "fixture_corrupt.pdf", report, 64)

    # Each suffix lies about bytes that are a real document of another kind, so the
    # sniffing is tested in all three directions rather than only where it is easy.
    (CONTENTS / "fixture_mislabelled.docx").write_bytes(report.read_bytes())
    (CONTENTS / "fixture_mislabelled.pptx").write_bytes(protocol.read_bytes())
    (CONTENTS / "fixture_mislabelled.pdf").write_bytes(overview.read_bytes())
    # And one that is no document at all, so "not a docx" is distinguishable from
    # "a docx that is really a pdf".
    (CONTENTS / "fixture_not_a.docx").write_bytes((CONTENTS / "fixture_measurements.csv").read_bytes())

    for pattern in ("fixture_*.docx", "fixture_*.pptx", "fixture_*.pdf"):
        for name in sorted(p.name for p in CONTENTS.glob(pattern)):
            print(f"wrote {name} ({(CONTENTS / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
