"""What a client sees when a read fails: a 500, an error id, no internals."""
import re


def assert_opaque_500(response, secret: str):
    """A 500 whose detail carries an 8-hex error id and NOT the exception
    text (which can hold paths, SQL or config on a public page)."""
    assert response.status_code == 500, (response.status_code, response.text[:200])
    detail = response.json()["detail"]
    assert re.search(r"error id [0-9a-f]{8}\b", detail), detail
    assert secret not in detail, detail
    return detail
