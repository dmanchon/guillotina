# this module closely mirrors aiohttp.web_exceptions
from guillotina.interfaces import IResponse
from guillotina.interfaces import IAioHTTPResponse
from guillotina.asgi import AsgiStreamWriter
from multidict import CIMultiDict
from typing import Optional
from zope.interface import classImplements
from zope.interface import implementer
from typing import Any
from aiohttp import hdrs

import warnings


@implementer(IResponse)
class Response(Exception):

    status_code: Optional[int] = None
    empty_body = False
    default_content: dict = {}  # noqa

    def __init__(self, *, content: dict=None,
                 headers: dict=None, status: int=None) -> None:
        '''
        :param content: content to serialize
        :param headers: headers to set on response
        :param status: customize the response status
        '''
        if self.empty_body:
            self.content = None
        else:
            self.content = content or self.default_content.copy()
        if headers is None:
            self.headers = CIMultiDict()  # type: ignore
        else:
            self.headers = CIMultiDict(headers)  # type: ignore
        if status is not None:
            if self.status_code:
                raise ValueError('Can not customize status code of this type')
            else:
                if status in (204, 205):
                    self.content = None
                self.status_code = status


@implementer(IAioHTTPResponse)
class StreamResponse():

    empty_body = False
    default_content: dict = {}  # noqa

    def __init__(self, *, content: dict=None,
                 headers: dict=None, status: int=None) -> None:
        '''
        :param content: content to serialize
        :param headers: headers to set on response
        :param status: customize the response status
        '''
        if self.empty_body:
            self.content = None
        else:
            self.content = content or self.default_content.copy()
        if headers is None:
            self.headers = CIMultiDict()  # type: ignore
        else:
            self.headers = CIMultiDict(headers)  # type: ignore

        if status is None:
            raise ValueError("Status is none")

        if status in (204, 205):
            self.content = None
        self.status_code = status

        self._payload_writer = None
        self._eof_sent = False
        self._keep_alive = True

    @property
    def prepared(self) -> bool:
        return self._payload_writer is not None

    def _generate_content_type_header(
            self,
            CONTENT_TYPE=hdrs.CONTENT_TYPE
        ) -> None:
        assert self._content_dict is not None
        assert self._content_type is not None
        params = '; '.join("{}={}".format(k, v)
                           for k, v in self._content_dict.items())
        if params:
            ctype = self._content_type + '; ' + params
        else:
            ctype = self._content_type
        self.headers[CONTENT_TYPE] = ctype

    async def _do_start_compression(self, coding) -> None:
        if coding != ContentCoding.identity:
            assert self._payload_writer is not None
            self.headers[hdrs.CONTENT_ENCODING] = coding.value
            self._payload_writer.enable_compression(coding.value)
            # Compressed payload may have different content length,
            # remove the header
            self.headers.popall(hdrs.CONTENT_LENGTH, None)

    async def _start_compression(self, request: 'BaseRequest') -> None:
        if self._compression_force:
            await self._do_start_compression(self._compression_force)
        else:
            accept_encoding = request.headers.get(
                hdrs.ACCEPT_ENCODING, '').lower()
            for coding in ContentCoding:
                if coding.value in accept_encoding:
                    await self._do_start_compression(coding)
                    return

    async def prepare(
            self,
            request: 'BaseRequest'
    ):
        if self._eof_sent:
            return None
        if self._payload_writer is not None:
            return self._payload_writer

        await request._prepare_hook(self)
        return await self._start(request)

    async def _start(self, request: 'BaseRequest'):
        self._req = request

        keep_alive = self._keep_alive
        if keep_alive is None:
            keep_alive = request.keep_alive
        self._keep_alive = keep_alive

        version = request.version
        writer = self._payload_writer = AsgiStreamWriter(request.send)

        headers = self.headers
        headers.setdefault(hdrs.CONTENT_TYPE, 'application/octet-stream')
        # headers.setdefault(hdrs.DATE, rfc822_formatted_time())
        # headers.setdefault(hdrs.SERVER, SERVER_SOFTWARE)

        # status line
        # status_line = 'HTTP/{}.{} {} {}'.format(
        #     version[0], version[1], self._status, self._reason)
        # await writer.write_headers(status_line, headers)

        from guillotina.asgi import headers_to_list

        await request.send({
            "type": "http.response.start",
            "headers": headers_to_list(headers),
            "status": self.status_code
        })

        return writer

    async def write(self, data: bytes) -> None:
        assert isinstance(data, (bytes, bytearray, memoryview)), \
            "data argument must be byte-ish (%r)" % type(data)

        if self._eof_sent:
            raise RuntimeError("Cannot call write() after write_eof()")
        if self._payload_writer is None:
            raise RuntimeError("Cannot call write() before prepare()")

        await self._payload_writer.write(data)

    async def drain(self) -> None:
        assert not self._eof_sent, "EOF has already been sent"
        assert self._payload_writer is not None, \
            "Response has not been started"
        warnings.warn("drain method is deprecated, use await resp.write()",
                      DeprecationWarning,
                      stacklevel=2)
        await self._payload_writer.drain()

    async def write_eof(self, data: bytes=b'') -> None:
        assert isinstance(data, (bytes, bytearray, memoryview)), \
            "data argument must be byte-ish (%r)" % type(data)

        if self._eof_sent:
            return

        assert self._payload_writer is not None, \
            "Response has not been started"

        await self._payload_writer.write_eof(data)
        self._eof_sent = True
        self._req = None
        self._body_length = self._payload_writer.output_size
        self._payload_writer = None

    def __repr__(self) -> str:
        if self._eof_sent:
            info = "eof"
        elif self.prepared:
            assert self._req is not None
            info = "{} {} ".format(self._req.method, self._req.path)
        else:
            info = "not prepared"
        return "<{} {} {}>".format(self.__class__.__name__,
                                   self.reason, info)

    def __getitem__(self, key: str) -> Any:
        return self._state[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._state[key] = value

    def __delitem__(self, key: str) -> None:
        del self._state[key]

    def __len__(self) -> int:
        return len(self._state)

    def __iter__(self):
        return iter(self._state)

    def __hash__(self) -> int:
        return hash(id(self))

    def __eq__(self, other: object) -> bool:
        return self is other


class ErrorResponse(Response):
    def __init__(self, type: str, message: str, *, reason=None,
                 content: dict=None, headers: dict=None,
                 status: int=500) -> None:
        '''
        :param type: type of error
        :param message: error message
        :param content: provide additional content
        :param headers: headers to set on response
        :param status: customize the response status
        '''
        if content is None:
            content = {}
        content['error'] = {
            'type': type,
            'message': message
        }
        if reason is not None:
            from guillotina.exc_resp import render_error_response
            content.update(render_error_response(type, reason))
        super().__init__(content=content, headers=headers, status=status)


class HTTPError(Response):
    """Base class for exceptions with status codes in the 400s and 500s."""


class HTTPRedirection(Response):
    """Base class for exceptions with status codes in the 300s."""


class HTTPSuccessful(Response):
    """Base class for exceptions with status codes in the 200s."""


class HTTPOk(HTTPSuccessful):
    status_code = 200


class HTTPCreated(HTTPSuccessful):
    status_code = 201


class HTTPAccepted(HTTPSuccessful):
    status_code = 202


class HTTPNonAuthoritativeInformation(HTTPSuccessful):
    status_code = 203


class HTTPNoContent(HTTPSuccessful):
    status_code = 204
    empty_body = True


class HTTPResetContent(HTTPSuccessful):
    status_code = 205
    empty_body = True


class HTTPPartialContent(HTTPSuccessful):
    status_code = 206


############################################################
# 3xx redirection
############################################################


class _HTTPMove(HTTPRedirection):

    def __init__(self, location: str, *,
                 content: dict=None, headers: dict=None) -> None:
        if not location:
            raise ValueError("HTTP redirects need a location to redirect to.")
        super().__init__(content=content, headers=headers)
        self.headers['Location'] = str(location)
        self.location = location


class HTTPMultipleChoices(_HTTPMove):
    '''
    :param location: where to redirect
    :param headers: additional headers to set
    '''

    status_code = 300


class HTTPMovedPermanently(_HTTPMove):
    '''
    :param location: where to redirect
    :param headers: additional headers to set
    '''

    status_code = 301


class HTTPFound(_HTTPMove):
    '''
    :param location: where to redirect
    :param headers: additional headers to set
    '''
    status_code = 302


# This one is safe after a POST (the redirected location will be
# retrieved with GET):
class HTTPSeeOther(_HTTPMove):
    '''
    :param location: where to redirect
    :param headers: additional headers to set
    '''
    status_code = 303


class HTTPNotModified(HTTPRedirection):
    # FIXME: this should include a date or etag header
    status_code = 304
    empty_body = True


class HTTPUseProxy(_HTTPMove):
    # Not a move, but looks a little like one
    status_code = 305


class HTTPTemporaryRedirect(_HTTPMove):
    status_code = 307


class HTTPPermanentRedirect(_HTTPMove):
    status_code = 308


############################################################
# 4xx client error
############################################################


class HTTPClientError(HTTPError):
    pass


class HTTPBadRequest(HTTPClientError):
    status_code = 400


class HTTPUnauthorized(HTTPClientError):
    status_code = 401


class HTTPPaymentRequired(HTTPClientError):
    status_code = 402


class HTTPForbidden(HTTPClientError):
    status_code = 403


class HTTPNotFound(HTTPClientError):
    status_code = 404


class InvalidRoute(HTTPNotFound):
    '''
    The defined route is invalid
    '''


class HTTPMethodNotAllowed(HTTPClientError):
    status_code = 405

    def __init__(self, method: str, allowed_methods: list, *,
                 content: dict=None, headers: dict=None) -> None:
        '''
        :param method: method not allowed
        :param allowed_methods: list of allowed methods
        :param content: content to serialize
        :param headers: headers to set on response
        '''
        allow = ','.join(sorted(allowed_methods))
        super().__init__(content=content, headers=headers)
        self.headers['Allow'] = allow
        self.allowed_methods = allowed_methods
        self.method = method.upper()


class HTTPNotAcceptable(HTTPClientError):
    status_code = 406


class HTTPProxyAuthenticationRequired(HTTPClientError):
    status_code = 407


class HTTPRequestTimeout(HTTPClientError):
    status_code = 408


class HTTPConflict(HTTPClientError):
    status_code = 409


class HTTPGone(HTTPClientError):
    status_code = 410


class HTTPLengthRequired(HTTPClientError):
    status_code = 411


class HTTPPreconditionFailed(HTTPClientError):
    status_code = 412


class HTTPRequestEntityTooLarge(HTTPClientError):
    status_code = 413


class HTTPRequestURITooLong(HTTPClientError):
    status_code = 414


class HTTPUnsupportedMediaType(HTTPClientError):
    status_code = 415


class HTTPRequestRangeNotSatisfiable(HTTPClientError):
    status_code = 416


class HTTPExpectationFailed(HTTPClientError):
    status_code = 417


class HTTPMisdirectedRequest(HTTPClientError):
    status_code = 421


class HTTPUnprocessableEntity(HTTPClientError):
    status_code = 422


class HTTPFailedDependency(HTTPClientError):
    status_code = 424


class HTTPUpgradeRequired(HTTPClientError):
    status_code = 426


class HTTPPreconditionRequired(HTTPClientError):
    status_code = 428


class HTTPTooManyRequests(HTTPClientError):
    status_code = 429


class HTTPRequestHeaderFieldsTooLarge(HTTPClientError):
    status_code = 431


class HTTPUnavailableForLegalReasons(HTTPClientError):
    status_code = 451

    def __init__(self, link, *, content=None, headers=None):
        super().__init__(content=content, headers=headers)
        self.headers['Link'] = '<%s>; rel="blocked-by"' % link
        self.link = link


############################################################
# 5xx Server Error
############################################################
#  Response status codes beginning with the digit "5" indicate cases in
#  which the server is aware that it has erred or is incapable of
#  performing the request. Except when responding to a HEAD request, the
#  server SHOULD include an entity containing an explanation of the error
#  situation, and whether it is a temporary or permanent condition. User
#  agents SHOULD display any included entity to the user. These response
#  codes are applicable to any request method.


class HTTPServerError(HTTPError):
    pass


class HTTPInternalServerError(HTTPServerError):
    status_code = 500


class HTTPNotImplemented(HTTPServerError):
    status_code = 501


class HTTPBadGateway(HTTPServerError):
    status_code = 502


class HTTPServiceUnavailable(HTTPServerError):
    status_code = 503


class HTTPGatewayTimeout(HTTPServerError):
    status_code = 504


class HTTPVersionNotSupported(HTTPServerError):
    status_code = 505


class HTTPVariantAlsoNegotiates(HTTPServerError):
    status_code = 506


class HTTPInsufficientStorage(HTTPServerError):
    status_code = 507


class HTTPNotExtended(HTTPServerError):
    status_code = 510


class HTTPNetworkAuthenticationRequired(HTTPServerError):
    status_code = 511
