"""Builds the Excel results workbook from the CSV tables in reports/tables.

    python scripts/build_excel_report.py

Run scripts/run_notebook.py first so the tables are current.
"""
from pathlib import Path

import pandas as pd
from openpyxl import Workbook
from openpyxl.drawing.image import Image
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parent.parent
TABLES = ROOT / "reports" / "tables"
FIGURES = ROOT / "reports" / "figures"

CONFIG = {
    "file": "Taiwan_Network_Optimization_Results.xlsx",
    "title": "Where Should a Distribution Centre Go? Network Design and Routing for Taiwan",
    "subtitle": "OR-Tools on real population and road data. The company, its volume of 20 million parcels a year and all costs are assumptions.",
    "findings": [
        "Recommended: four DCs at Taoyuan, Changhua, Pingtung and Hualien, about NT$634M a year or NT$31.7 a parcel.",
        "Three and four DCs cost the same (within 0.2%), but four bring 98.5% of the population within two truck hours, against 77.7% with three.",
        "Intuitive big-city networks cost NT$22-48M a year more than the optimised network, under the assumed rents.",
        "A per-kilometre delivery estimate gave NT$36M a year at four DCs. Routed trucks cost NT$184M. The chosen network moved far less: 0.7% in cost.",
        "2.2% of parcels, on the east coast, drive about 16% of cost. A carrier below about NT$225 a parcel would be cheaper than running the east in-house.",
        "Routing saves about 15% against a nearest-neighbour rule, or about NT$33M a year. The network model is solved to proven optimality, while routes are good solutions and not proven best.",
    ],
    "sheets": {
        "Geography": ("geography_profile.csv", "townships, and road against straight-line distance"),
        "Network frontier": ("network_frontier.csv", "best network for each DC count, under both delivery rates"),
        "Alternatives": ("alternative_networks.csv", "networks a planner might propose, against the optimised one"),
        "Calibration": ("calibration_history.csv", "delivery rate through each round of the feedback loop"),
        "Delivery estimate gap": ("delivery_estimate_gap.csv", "model delivery cost against routed trucks (NT$ million)"),
        "Cost to serve": ("cost_to_serve_by_dc.csv", "cost per parcel at each DC of the recommended network"),
        "East without a DC": ("east_coast_without_dc.csv", "when the east can be served from the west"),
        "East outsourcing": ("east_coast_outsourcing.csv", "break-even price for a carrier on the east coast"),
        "Routing methods": ("routing_methods.csv", "three routing methods on five simulated days"),
        "Routing convergence": ("routing_convergence.csv", "route cost against search time on the largest DC"),
        "Solver benchmarks": ("solver_benchmarks.csv", "the routing solver on public instances with known best values"),
        "Losing a DC": ("resilience_one_dc_lost.csv", "what happens if each DC is lost"),
        "Sensitivity": ("sensitivity.csv", "the best network under other assumptions"),
    },
    "figure": "03_network_maps.png",
    "source": "Population and boundaries: Ministry of the Interior via data.gov.tw. Road data: OpenStreetMap contributors through OSRM (ODbL).",
}

HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=16, color="1F4E79")


def number_format(column, series):
    name = column.lower()
    if not pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
        return None
    if "pct" in name:
        return '0.0"%"'
    if "per_parcel_km" in name:
        return "0.000"
    if "per_parcel" in name or "price" in name:
        return '"NT$"#,##0.0'
    if "cost" in name or "saving" in name or "extra_vs" in name:
        return '"NT$"#,##0'
    if series.abs().max() >= 1000:
        return "#,##0"
    if pd.api.types.is_integer_dtype(series):
        return "0"
    return "0.00"


def add_table_sheet(wb, title, frame):
    ws = wb.create_sheet(title[:31])
    ws.append(list(frame.columns))
    for row in frame.itertuples(index=False):
        ws.append([None if pd.isna(v) else (v.item() if hasattr(v, "item") else v) for v in row])
    for cell in ws[1]:
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height = 48
    for idx, column in enumerate(frame.columns, start=1):
        fmt = number_format(column, frame[column])
        letter = get_column_letter(idx)
        if fmt:
            for cell in ws[letter][1:]:
                cell.number_format = fmt
        longest = max((len(str(v)) for v in frame[column].head(200)), default=8)
        ws.column_dimensions[letter].width = min(max(longest + 2, 14), 60)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = ws.dimensions


def main():
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws["A1"] = CONFIG["title"]
    ws["A1"].font = TITLE_FONT
    ws["A2"] = CONFIG["subtitle"]
    ws["A2"].font = Font(italic=True, color="555555")
    row = 4
    ws.cell(row, 1, "Key findings").font = Font(bold=True, size=12)
    for finding in CONFIG["findings"]:
        row += 1
        ws.cell(row, 1, f"• {finding}").alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[row].height = 32
    row += 2
    ws.cell(row, 1, "Sheets in this workbook").font = Font(bold=True, size=12)
    for sheet, (_, description) in CONFIG["sheets"].items():
        row += 1
        ws.cell(row, 1, f"{sheet}: {description}")
    row += 2
    ws.cell(row, 1, "Source: " + CONFIG["source"]).font = Font(color="555555")
    ws.column_dimensions["A"].width = 120
    if (FIGURES / CONFIG["figure"]).exists():
        image = Image(str(FIGURES / CONFIG["figure"]))
        scale = 620 / image.width
        image.width, image.height = image.width * scale, image.height * scale
        ws.add_image(image, f"A{row + 2}")

    for sheet, (csv_name, _) in CONFIG["sheets"].items():
        add_table_sheet(wb, sheet, pd.read_csv(TABLES / csv_name))

    out = ROOT / "reports" / CONFIG["file"]
    wb.save(out)
    print(f"Wrote {out.relative_to(ROOT)} with {len(wb.sheetnames)} sheets")


if __name__ == "__main__":
    main()
