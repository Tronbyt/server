from flask.testing import FlaskClient


def test_register_login_logout(client: FlaskClient) -> None:
    response = client.get("/auth/register")
    assert response.status_code == 200
    response = client.post(
        "/auth/register", data={"username": "testuser", "password": "password"}
    )
    # Ensure response is a redirect to /auth/login

    # assert response.status_code == 302
    assert response.headers["Location"] == "/auth/login"

    # test successful login of new user
    response = client.post(
        "/auth/login", data={"username": "testuser", "password": "password"}
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"

    response = client.get("/auth/logout")
    assert response.status_code == 302  # should redirect to login
    # make sure redirected to auth/login
    assert response.headers["Location"] == "/auth/login"


def test_login_with_wrong_password(client: FlaskClient) -> None:
    response = client.post(
        "/auth/login", data={"username": "testuser", "password": "BADDPASSWORD"}
    )
    print(response.text)
    assert "Incorrect username/password." in response.text


def test_unauth_index(client: FlaskClient) -> None:
    response = client.get("/")
    assert response.status_code == 302  # should redirect to login
    assert "auth/login" in response.text
