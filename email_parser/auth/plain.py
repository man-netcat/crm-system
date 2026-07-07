from .base import AuthProvider


class PlainAuth(AuthProvider):
    def __init__(self, username: str, password: str):
        self._username = username
        self._password = password

    def get_username(self) -> str:
        return self._username

    def get_password(self) -> str:
        return self._password

    def name(self) -> str:
        return "plain"
