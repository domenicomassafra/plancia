"""Keep backend-only tests independent of the separately built Vite bundle.

Production still fails closed with HTTP 503 when a built web bundle is absent.
Frontend build correctness is checked by ``npm run build`` and ``npm test``.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="session", autouse=True)
def web_index_for_backend_tests(tmp_path_factory: pytest.TempPathFactory):
    from backend import server

    if (server.WEB_DIST / "index.html").is_file():
        yield
        return

    test_dist = tmp_path_factory.mktemp("web-test-dist")
    (test_dist / "index.html").write_text(
        '<!doctype html><html><head><title>Plancia test</title></head>'
        '<body><div id="root"></div></body></html>',
        encoding="utf-8",
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(server, "WEB_DIST", test_dist)
        yield
