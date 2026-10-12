"""Write the spreadsheet fixtures the suites profile.

Binary fixtures that cannot be read in a diff have to be regenerable, otherwise nobody
can tell later what a test is actually asserting against. Run after changing anything
the assertions in tests/kernel-api.mjs pin:

    .venv/Scripts/python.exe scripts/make_xlsx_fixtures.py

fixture_measurements.xlsx carries the same table as fixture_measurements.csv, cell for
cell, so the suite can assert that a sheet and a CSV of the same data profile alike.

openpyxl writes the OOXML files. xlwt writes the legacy BIFF one, which is the only way
to produce a real .xls here; calamine reads it through its CFB parser, and the suite
pins that rather than taking it on trust.
"""

from __future__ import annotations

import datetime
from pathlib import Path

import xlwt
from openpyxl import Workbook
from openpyxl.utils import get_column_letter

CONTENTS = Path(__file__).resolve().parents[1] / "contents"

MEASUREMENTS = [
    ["taken_at", "sensor_id", "measurement_id", "value", "unit", "note"],
    [datetime.date(2024, 5, 1), "S-11", "m-001", 21.5, "degC", None],
    [datetime.date(2024, 5, 2), "S-11", "m-002", 22.0, "degC", "calibrated"],
    [datetime.date(2024, 5, 3), "S-12", "m-003", 21.5, "degC", None],
    [datetime.date(2024, 5, 4), "S-12", "m-004", 20.25, "degC", None],
]

SENSORS = [
    ["sensor_id", "placed_at", "depth_m"],
    ["S-11", "basin north", 1.5],
    ["S-12", "basin south", 2.0],
]


def write_sheet(sheet, rows: list[list]) -> None:
    for row in rows:
        sheet.append(row)


def single_sheet(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "measurements"
    write_sheet(sheet, MEASUREMENTS)
    workbook.save(path)


def multi_sheet(path: Path) -> None:
    workbook = Workbook()
    first = workbook.active
    first.title = "measurements"
    write_sheet(first, MEASUREMENTS)
    write_sheet(workbook.create_sheet("sensors"), SENSORS)
    workbook.create_sheet("blank")
    workbook.save(path)


def merged_header(path: Path) -> None:
    """A title merged across the real header, which is what an exported report looks like."""
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "report"
    sheet["A1"] = "Measurements 2024"
    sheet.merge_cells(f"A1:{get_column_letter(len(SENSORS[0]))}1")
    write_sheet(sheet, SENSORS)
    workbook.save(path)


def legacy_xls(path: Path) -> None:
    workbook = xlwt.Workbook()
    sheet = workbook.add_sheet("legacy")
    for row_index, row in enumerate(SENSORS):
        for column_index, value in enumerate(row):
            sheet.write(row_index, column_index, value)
    workbook.save(str(path))


def corrupt(path: Path, source: Path) -> None:
    """A truncated zip: the suffix promises xlsx and the bytes do not deliver one."""
    data = source.read_bytes()
    path.write_bytes(data[: len(data) // 3])


def main() -> None:
    single = CONTENTS / "fixture_measurements.xlsx"
    single_sheet(single)
    multi_sheet(CONTENTS / "fixture_workbook.xlsx")
    merged_header(CONTENTS / "fixture_merged_header.xlsx")
    legacy_xls(CONTENTS / "fixture_legacy.xls")
    corrupt(CONTENTS / "fixture_corrupt.xlsx", single)

    for name in sorted(p.name for p in CONTENTS.glob("fixture_*.xls*")):
        print(f"wrote {name} ({(CONTENTS / name).stat().st_size} bytes)")


if __name__ == "__main__":
    main()
