from abc import ABC, abstractmethod
from typing import Callable


class AuthProvider(ABC):
    @abstractmethod
    def get_username(self) -> str:
        ...

    @abstractmethod
    def get_password(self) -> str:
        ...

    def get_authenticator(self) -> Callable | None:
        return None

    @abstractmethod
    def name(self) -> str:
        ...
