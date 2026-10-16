from zipfile import ZipFile
import fitz
from lxml import etree
from engine.pptx_output import create_pptx, eligible, P, A


def test_landscape_pages_keep_native_text_and_background(tmp_path):
    path = tmp_path / 'source.pdf'
    with fitz.open() as pdf:
        page = pdf.new_page(width=720, height=405)
        page.insert_text((30, 60), 'Editable slide text')
        pdf.save(path)
    assert eligible(path)
    output = tmp_path / 'output.pptx'
    create_pptx(path, output)
    with ZipFile(output) as archive:
        root = etree.fromstring(archive.read('ppt/slides/slide1.xml'))
        assert 'Editable slide text' in ''.join(root.itertext())
        assert root.find('.//{' + A + '}t') is not None
        assert root.find('.//{' + P + '}pic') is not None
        assert 'ppt/slideMasters/slideMaster1.xml' in archive.namelist()
        assert 'ppt/slideLayouts/slideLayout7.xml' in archive.namelist()


def test_portrait_and_mixed_geometry_do_not_offer_slides(tmp_path):
    path = tmp_path / 'source.pdf'
    with fitz.open() as pdf:
        pdf.new_page(width=720, height=405)
        pdf.new_page(width=420, height=595)
        pdf.save(path)
    assert not eligible(path)
