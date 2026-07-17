import base64
import os
from io import BytesIO
from datetime import date

from openpyxl import load_workbook

_TEMPLATE_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__),
    "..", "..", "..", "_Templates", "StartingTemplate_20260410.xlsx"
))


def build_excel(rows):
    """Load the template, append data rows, return (filename, base64_string)."""
    wb = load_workbook(_TEMPLATE_PATH)
    ws = wb.active

    for row in rows:
        ws.append([row["Job"], row["Phase"], row["RemainingUnits"]])

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    b64      = base64.b64encode(buf.read()).decode("utf-8")
    filename = f"TimeSheets_{date.today().strftime('%Y%m%d')}.xlsx"
    return filename, b64
