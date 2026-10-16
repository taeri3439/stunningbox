"""Export policy based on implemented writers and actual output structure."""
import hashlib


def options(run_dir, report):
    """The lab currently accepts PDF. Never advertise unimplemented conversions."""
    result = []
    docx = report.get('docx_output') or {}
    if (run_dir / 'output.pdf').exists():
        stale = docx.get('edited') and docx.get('preview_status') != 'ready'
        result.append({'format': 'pdf', 'label': 'PDF · 원본 형식',
                       'available': not stale,
                       'description': '편집기에서 저장·미리보기를 갱신하세요.' if stale else '페이지 배치를 유지한 결과입니다.'})
    if (run_dir / 'layout.docx').exists() and docx.get('editable_lines', 0) > 0:
        result.append({'format': 'docx', 'label': 'DOCX · 텍스트 편집', 'available': True,
                       'description': '줄별 텍스트 상자를 편집합니다. 표 선·그림은 배경 이미지이며 Word 표 셀은 아닙니다.'})
    slides = report.get('slide_output') or {}
    if (run_dir / 'layout.pptx').exists() and slides.get('verified') and not (docx.get('edited') and docx.get('preview_status') != 'ready'):
        if slides.get('source_hash') == hashlib.sha256((run_dir / 'output.pdf').read_bytes()).hexdigest():
            result.append({'format': 'pptx', 'label': 'PPTX · 가로형 페이지', 'available': True,
                           'description': '페이지를 슬라이드로 보존합니다. 글자는 텍스트 상자, 표 선·그림은 배경 이미지입니다. 폰트와 넘침을 확인하세요.'})
    return {'source_format': 'pdf', 'default_format': 'pdf', 'options': result}
