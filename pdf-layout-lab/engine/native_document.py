"""Preserve source packages; replace only native text nodes, never rebuild pages."""
import re
from zipfile import ZipFile, BadZipFile
from lxml import etree

FORMATS = {'doc', 'docx', 'hwp', 'hwpx', 'pptx'}
EDITABLE = {'docx', 'hwpx', 'pptx'}
NS = {'docx': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
      'pptx': 'http://schemas.openxmlformats.org/drawingml/2006/main',
      'hwpx': 'http://www.hancom.co.kr/hwpml/2011/paragraph'}


def parts(names, kind):
    patterns = {'docx': r'word/(document|header\d+|footer\d+|footnotes|endnotes)\.xml',
                'pptx': r'ppt/slides/slide\d+\.xml', 'hwpx': r'Contents/section\d+\.xml'}
    return [name for name in names if re.fullmatch(patterns[kind], name)]


def validate(path, kind):
    if kind not in FORMATS:
        raise ValueError('지원하지 않는 입력 형식입니다.')
    if kind in {'doc', 'hwp'}:
        if path.read_bytes()[:8] != bytes.fromhex('D0CF11E0A1B11AE1'):
            raise ValueError('확장자와 파일 내용이 일치하지 않습니다. 암호화되지 않은 원본을 사용하세요.')
        import olefile
        try:
            with olefile.OleFileIO(str(path)) as archive:
                stream = 'WordDocument' if kind == 'doc' else 'FileHeader'
                if not archive.exists(stream):
                    raise ValueError('확장자와 문서 구조가 일치하지 않습니다.')
                if kind == 'hwp':
                    header = archive.openstream(stream).read(40)
                    if not header.startswith(b'HWP Document File') or len(header) < 40:
                        raise ValueError('지원하는 HWP 5 문서가 아닙니다.')
                    if int.from_bytes(header[36:40], 'little') & 2:
                        raise ValueError('암호화된 HWP는 지원하지 않습니다.')
                else:
                    header = archive.openstream(stream).read(12)
                    if len(header) < 12 or int.from_bytes(header[10:12], 'little') & 0x8100:
                        raise ValueError('암호화된 DOC는 지원하지 않습니다.')
        except OSError as exc:
            raise ValueError('문서 컨테이너가 손상되었습니다.') from exc
        return
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > 20000 or sum(x.file_size for x in entries) > 250 * 1024 * 1024:
                raise ValueError('압축 해제 크기가 너무 큽니다.')
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('중복된 패키지 항목이 있습니다.')
            if any('vbaproject' in n.lower() or '_xmlsignatures/' in n.lower() for n in names):
                raise ValueError('매크로 또는 서명된 문서는 지원하지 않습니다.')
            required = {'docx': 'word/document.xml', 'pptx': 'ppt/presentation.xml', 'hwpx': 'Contents/content.hpf'}[kind]
            if required not in names or not parts(names, kind):
                raise ValueError('확장자에 맞는 문서 구조가 없습니다.')
            for name in parts(names, kind):
                parse(archive.read(name))
    except BadZipFile as exc:
        raise ValueError('유효한 문서 패키지가 아닙니다.') from exc


def parse(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
    root = etree.fromstring(data, parser)
    if root.getroottree().docinfo.doctype:
        raise ValueError('DTD가 포함된 문서는 지원하지 않습니다.')
    return root


def text_nodes(root, kind):
    return [n for n in root.iter('{' + NS[kind] + '}t') if n.text and n.text.strip()]


def translate_package(source, output, kind, translate):
    validate(source, kind)
    if kind not in EDITABLE:
        raise ValueError('이 형식은 원본 형식 번역 저장 엔진이 없습니다.')
    changed, count, unchanged = {}, 0, 0
    with ZipFile(source) as archive:
        for name in parts(archive.namelist(), kind):
            root = parse(archive.read(name))
            nodes = text_nodes(root, kind)
            # Keep run boundaries and their individual styles. Translation may reflow.
            for offset in range(0, len(nodes), 40):
                batch = nodes[offset:offset + 40]
                before = [node.text for node in batch]
                after = translate(before)
                if len(after) != len(before) or any(not isinstance(x, str) or not x.strip() for x in after):
                    raise ValueError('번역 결과의 텍스트 개수 또는 내용이 올바르지 않습니다.')
                for node, original, text in zip(batch, before, after):
                    if original == text:
                        unchanged += 1
                    node.text = text
                    if kind == 'docx':
                        node.set('{http://www.w3.org/XML/1998/namespace}space', 'preserve')
                    count += 1
            if nodes:
                if kind == 'hwpx':
                    # Cached line positions refer to the original text; let Hancom reflow.
                    for cached in list(root.iter('{' + NS[kind] + '}linesegarray')):
                        cached.getparent().remove(cached)
                changed[name] = etree.tostring(root, xml_declaration=True, encoding='UTF-8', standalone=True)
        if count == 0:
            raise ValueError('편집 가능한 텍스트가 없습니다. 이미지 글자는 이 경로에서 번역하지 않습니다.')
        with ZipFile(output, 'w') as destination:
            for entry in archive.infolist():
                # Media, styles, relationships, geometry and embedded objects remain byte-identical.
                destination.writestr(entry, changed.get(entry.filename, archive.read(entry.filename)))
    return {'segments': count, 'unchanged': unchanged, 'changed_parts': list(changed),
            'warnings': ['서식·개체 구조는 유지했지만 번역 길이와 폰트에 따라 줄바꿈·페이지가 달라질 수 있습니다.',
                         '이미지 글자, 차트 데이터, 슬라이드 마스터 및 삽입 개체의 텍스트는 번역 범위에서 제외됩니다.']}


def candidates(kind):
    # OnlyOffice exports OOXML/PDF, not legacy DOC/HWP or HWPX.
    return ['pdf'] if kind in {'docx', 'pptx'} else ['pdf', 'docx']
