"""build_editor_config sets a fit-to-width default zoom for the embedded editor."""
from pathlib import Path

import onlyoffice


def test_editor_config_sets_fit_to_width_zoom(tmp_path: Path):
    docx = tmp_path / "doc.en.docx"
    docx.write_bytes(b"PK\x03\x04 stub docx")  # a real file so document_key() can stat it
    cfg = onlyoffice.build_editor_config(
        doc_id="abc123", lang="en", title="Sample", docx_path=str(docx),
    )
    customization = cfg["editorConfig"]["customization"]
    # -2 = OnlyOffice "fit page width to editor" (a true fit mode, not a fixed %),
    # honoured by the Community 9.4.0 build for the "word" documentType.
    assert customization["zoom"] == -2
    # the existing chrome-trimming flags must remain
    assert customization["compactHeader"] is True
