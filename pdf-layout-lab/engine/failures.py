"""Safe job diagnostics: never expose SDK request bodies or credentials."""
import errno
import socket
import ssl


def file_failure(exc):
    if not isinstance(exc, FileNotFoundError):
        return None
    # Paths can contain private names. Expose only length and OS error code.
    path_length = max(len(str(getattr(exc, name, None) or '')) for name in ('filename', 'filename2'))
    return {'error_code': 'file_not_found', 'path_length': path_length,
            'os_error': getattr(exc, 'winerror', None) or exc.errno,
            'message': '처리 중 파일을 읽거나 저장하지 못했습니다 (FileNotFoundError). '
                       f'대상 경로 길이: {path_length}자. 경로 길이·폴더 존재 여부를 확인하세요. '
                       '이전 결과와 저장 완료된 캐시는 유지됩니다.'}


def api_failure(exc):
    chain, seen = [], set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    names = {cls.__name__ for item in chain for cls in type(item).__mro__}
    if 'APIConnectionError' in names:
        if any(getattr(item, 'winerror', None) == 10013 or
               isinstance(item, PermissionError) and item.errno == errno.EACCES for item in chain):
            code, message = 'network_blocked', '실행 환경이 번역 API 연결을 차단했습니다 (소켓 접근 거부). 서버를 외부 API 연결이 허용된 일반 터미널에서 다시 실행하세요.'
        elif any(isinstance(item, ssl.SSLError) for item in chain):
            code, message = 'tls_error', '번역 API의 보안 연결에 실패했습니다. 인증서와 회사 프록시 설정을 확인하세요.'
        elif any(isinstance(item, socket.gaierror) for item in chain):
            code, message = 'dns_error', '번역 API 주소를 찾지 못했습니다. 인터넷 연결과 DNS 설정을 확인하세요.'
        elif names & {'APITimeoutError', 'ConnectTimeout', 'ReadTimeout', 'TimeoutError'}:
            code, message = 'api_timeout', '번역 API 연결 또는 응답 대기 시간이 초과되었습니다. 잠시 후 다시 시도하세요.'
        else:
            code, message = 'api_connection', '번역 API에 연결하지 못했습니다. 인터넷·프록시·서버 실행 환경을 확인한 뒤 다시 시도하세요.'
    elif 'AuthenticationError' in names:
        code, message = 'api_authentication', '번역 API 인증에 실패했습니다. 서버의 API 키 설정을 확인하세요.'
    elif 'RateLimitError' in names:
        code, message = 'api_limit', '번역 API 요청 또는 사용량 한도에 도달했습니다. 제공사의 사용량·결제 상태를 확인한 뒤 다시 시도하세요.'
    else:
        return None
    return {'error_code': code, 'error_types': [type(item).__name__ for item in chain],
            'message': message + ' 저장 완료된 번역 캐시만 재사용되며, 저장되지 않은 번역은 다시 처리합니다.'}
