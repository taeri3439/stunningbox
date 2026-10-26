import errno
import socket
import ssl

import httpx
import pytest
from openai import APIConnectionError, APITimeoutError, AuthenticationError, RateLimitError

from engine.failures import api_failure


@pytest.mark.parametrize('cause,code', [
    (PermissionError(errno.EACCES, 'secret request'), 'network_blocked'),
    (socket.gaierror(-2, 'secret host'), 'dns_error'),
    (ssl.SSLError('secret proxy'), 'tls_error'),
    (httpx.ConnectTimeout('secret URL'), 'api_timeout'),
    (httpx.ConnectError('secret URL'), 'api_connection'),
])
def test_connection_causes_are_safe_and_actionable(cause, code):
    exc = APIConnectionError(request=httpx.Request('POST', 'https://example.com/secret'))
    wrapped = httpx.ConnectError('secret request')
    wrapped.__cause__ = cause
    exc.__cause__ = wrapped
    result = api_failure(exc)
    assert result['error_code'] == code
    assert 'secret' not in str(result)
    assert '저장 완료된 번역 캐시만' in result['message']


@pytest.mark.parametrize('error,status,code', [
    (AuthenticationError, 401, 'api_authentication'),
    (RateLimitError, 429, 'api_limit'),
])
def test_auth_and_limits_are_not_connection_failures(error, status, code):
    response = httpx.Response(status, request=httpx.Request('POST', 'https://example.com'))
    result = api_failure(error('secret', response=response, body={'key': 'secret'}))
    assert result['error_code'] == code
    assert 'secret' not in str(result)


def test_sdk_timeout_and_unrelated_error():
    assert api_failure(APITimeoutError(request=httpx.Request('POST', 'https://example.com')))['error_code'] == 'api_timeout'
    assert api_failure(ValueError('unrelated')) is None
