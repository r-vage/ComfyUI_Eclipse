# Stable pipe construction shared by all diffusion model loaders.


class _OmitType:
    __slots__ = ()

    def __repr__(self) -> str:
        return "OMIT"

    def __bool__(self) -> bool:
        return False


OMIT = _OmitType()


def build_pipe(**kwargs) -> dict:
    return {key: value for key, value in kwargs.items() if value is not OMIT}
