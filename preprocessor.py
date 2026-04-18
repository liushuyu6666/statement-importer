"""Pre-processing steps applied to the statements folder before parsing."""

import zipfile
from pathlib import Path


class StatementPreprocessor:
    """Pre-processing steps applied to the statements folder before parsing."""

    @staticmethod
    def extract_zips(folder: Path) -> int:
        """Extract PDFs from every .zip in folder, flattened (no subfolders).

        Only .pdf entries are extracted; directories and non-PDF files inside
        the archive are ignored. Existing PDFs with the same name are not
        overwritten. Returns the number of PDFs extracted.
        """
        if not folder.is_dir():
            return 0
        extracted = 0
        for zip_path in sorted(folder.glob("*.zip")):
            with zipfile.ZipFile(zip_path) as zf:
                # infolist() is already flat: a PDF at "sub/file.pdf" appears
                # as its own entry alongside the "sub/" directory marker, so
                # we skip dir markers and strip folder prefixes via Path.name.
                for info in zf.infolist():
                    if info.is_dir():
                        continue
                    name = Path(info.filename).name
                    if not name.lower().endswith(".pdf"):
                        continue
                    dest = folder / name
                    if dest.exists():
                        continue
                    with zf.open(info) as src, open(dest, "wb") as out:
                        out.write(src.read())
                    extracted += 1
        return extracted
