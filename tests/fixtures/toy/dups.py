try:
    def compat():
        return 1
except ImportError:
    def compat():
        return 2


async def fetch_async():
    return 1


class Thing:
    @property
    def value(self):
        return 1

    @value.setter
    def value(self, v):
        pass


from typing import overload


@overload
def over(x: int) -> int: ...
@overload
def over(x: str) -> str: ...
def over(x):
    return x
