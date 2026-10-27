"""Keep connector identifiers/content out of routine access/error logs."""
import logging
from fastapi import HTTPException
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError


class ConnectorAccessFilter(logging.Filter):
    def filter(self,record):
        if isinstance(record.args,tuple) and len(record.args)==5:
            args=list(record.args);path=str(args[2])
            if (path.startswith(('/api/connector','/api/incoming-assessments','/app/connector','/app/cases/incoming', '/api/platform/connectors', '/api/platform/connector-events'))
                    or path.startswith('/app/cases/new?incoming_assessment_id=')):
                args[2]='/connector/[redacted]';record.args=tuple(args)
        return True


def install_access_filter():
    logger=logging.getLogger('uvicorn.access')
    if not any(isinstance(item,ConnectorAccessFilter) for item in logger.filters):
        logger.addFilter(ConnectorAccessFilter())


class ConnectorRoute(APIRoute):
    def get_route_handler(self):
        handler=super().get_route_handler()
        async def protected(request):
            try:
                response = await handler(request)
                response.headers['Cache-Control'] = 'no-store, private'
                return response
            except RequestValidationError:
                return JSONResponse({'detail': 'Invalid connector request.'}, status_code=422,
                                    headers={'Cache-Control': 'no-store, private'})
            except HTTPException as exc:
                return JSONResponse({'detail': exc.detail}, status_code=exc.status_code,
                                    headers={**(exc.headers or {}), 'Cache-Control': 'no-store, private'})
            except Exception:
                # Exception/SQL representations may contain source HTML or tokens.
                logging.getLogger('go360.connector').warning('Connector operation temporarily unavailable')
                return JSONResponse({'detail':'Connector operation temporarily unavailable. Retry the same job.'},
                                    status_code=503,headers={'Cache-Control':'no-store'})
        return protected
