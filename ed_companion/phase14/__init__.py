__all__ = ["CockpitController"]


def __getattr__(name):
    if name == "CockpitController":
        from .controller import CockpitController
        return CockpitController
    raise AttributeError(name)
