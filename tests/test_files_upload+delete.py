import os
from io import BytesIO
from . import utils

def test_upload_and_delete(client):
    client.post("/auth/register", data={"username": "testuser", "password": "password"})
    client.post("/auth/login",    data={"username": "testuser", "password": "password"})

    data = dict(
        file=(BytesIO(b'my file contents'), "report.star"),
    )

    r = client.post("/uploadapp", content_type='multipart/form-data', data=data)  

    assert "report.star" in utils.get_user_uploads_list()

    r = client.get("/deleteupload/report.star")

    assert "report.star" not in utils.get_user_uploads_list()

    # test rejected bad extension
    data = dict(
        file=(BytesIO(b'my file contents'), "report.exe"),
    )

    r = client.post("/uploadapp", content_type='multipart/form-data', data=data)
    assert "report.exe" not in utils.get_user_uploads_list()

