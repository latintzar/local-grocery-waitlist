from contextlib import contextmanager, asynccontextmanager
import httpx
@contextmanager
def http_client_sync(timeout=10, **kwargs):
    with httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False, **kwargs) as client:
        yield client
@asynccontextmanager
async def http_client(timeout=10, **kwargs):
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False, **kwargs) as client:
        yield client
