"""Mock platforms: real permission semantics in, real API shapes out.

Each mock is a small FastAPI app over the shared CompanyStore. The adapters talk to
them over HTTP (in-process ASGI transport in the demo), so a real adapter is a near
drop-in: same calls, different base URL and credentials.
"""
