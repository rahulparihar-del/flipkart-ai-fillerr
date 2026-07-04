"""Apply fill instructions to the original .xls in place via LibreOffice macro.

The macro is generic: it reads /tmp/kk_fill.tsv (sheet<TAB>row<TAB>col<TAB>kind<TAB>value)
so the original .xls keeps ALL sheets, validations and structure.
"""
import os
import platform
import shutil
import subprocess
import threading
from pathlib import Path

_LOCK = threading.Lock()
TSV_PATH = "/tmp/kk_fill.tsv"

MACRO = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE script:module PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "module.dtd">
<script:module xmlns:script="http://openoffice.org/2000/script" script:name="Module1" script:language="StarBasic">
    Sub ApplyFills()
      Dim iFile As Integer, sLine As String, parts
      Dim oSheet
      iFile = FreeFile
      Open "/tmp/kk_fill.tsv" For Input As #iFile
      Do While Not EOF(iFile)
        Line Input #iFile, sLine
        If Len(sLine) &gt; 0 Then
          parts = Split(sLine, Chr(9))
          oSheet = ThisComponent.Sheets.getByName(parts(0))
          If parts(3) = "n" Then
            oSheet.getCellByPosition(CLng(parts(2)), CLng(parts(1))).setValue(CDbl(parts(4)))
          Else
            oSheet.getCellByPosition(CLng(parts(2)), CLng(parts(1))).setString(parts(4))
          End If
        End If
      Loop
      Close #iFile
      ThisComponent.store()
      ThisComponent.close(True)
    End Sub
</script:module>"""

XLB = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE library:library PUBLIC "-//OpenOffice.org//DTD OfficeDocument 1.0//EN" "library.dtd">
<library:library xmlns:library="http://openoffice.org/2000/library" library:name="Standard" library:readonly="false" library:passwordprotected="false">
 <library:element library:name="Module1"/>
</library:library>"""


def _macro_dir() -> str:
    base = (
        "~/Library/Application Support/LibreOffice/4/user/basic/Standard"
        if platform.system() == "Darwin"
        else "~/.config/libreoffice/4/user/basic/Standard"
    )
    return os.path.expanduser(base)


def ensure_macro():
    d = _macro_dir()
    if not os.path.exists(d):
        subprocess.run(["soffice", "--headless", "--terminate_after_init"],
                       capture_output=True, timeout=60)
        os.makedirs(d, exist_ok=True)
    Path(d, "Module1.xba").write_text(MACRO)
    xlb = Path(d, "script.xlb")
    if not xlb.exists():
        xlb.write_text(XLB)


def sanitize(v) -> str:
    return str(v).replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def apply_fills(original_xls: str, out_xls: str, instructions: list, timeout=180):
    """instructions: [(sheet, row, col, kind, value)] kind: 's' string | 'n' number."""
    with _LOCK:
        ensure_macro()
        lines = []
        for sheet, row, col, kind, value in instructions:
            if kind == "n":
                lines.append(f"{sheet}\t{row}\t{col}\tn\t{float(value)}")
            else:
                lines.append(f"{sheet}\t{row}\t{col}\ts\t{sanitize(value)}")
        Path(TSV_PATH).write_text("\n".join(lines), encoding="utf-8")

        shutil.copy(original_xls, out_xls)
        os.chmod(out_xls, 0o644)
        cmd = [
            "soffice", "--headless", "--norestore",
            "vnd.sun.star.script:Standard.Module1.ApplyFills?language=Basic&location=application",
            str(Path(out_xls).absolute()),
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if res.returncode != 0:
            raise RuntimeError(f"LibreOffice fill failed: {res.stderr[:300]}")
    return out_xls
