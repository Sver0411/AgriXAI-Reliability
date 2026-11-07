"""Preview all official archive members, including each workbook tab."""
import io
from pathlib import Path
from zipfile import ZipFile

import openpyxl

archive = Path(__file__).resolve().parents[1] / "raw/9hyv69sm82_v1.zip"
with ZipFile(archive) as z:
    for name in sorted(z.namelist()):
        path = Path(name)
        if path.suffix not in {".csv", ".xlsx"}:
            raise ValueError(name)
        print("\n", "/".join(path.parts[1:]))
        if path.suffix == ".csv":
            print(z.read(name).decode("utf-8-sig", errors="replace").splitlines()[:5])
            continue
        book = openpyxl.load_workbook(io.BytesIO(z.read(name)), read_only=True, data_only=True)
        for sheet in book:
            print(sheet.title, sheet.max_row, sheet.max_column)
            for i, row in enumerate(sheet.values, 1):
                if i > (80 if path.stem == "Info" else 5):
                    break
                cells = " | ".join(f"{j}:{v}" for j, v in enumerate(row, 1) if v is not None)
                if cells:
                    print(i, cells[:1800])
        book.close()
