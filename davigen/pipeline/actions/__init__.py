"""Every action davigen knows. Importing this package registers them all (core.register)."""

from . import collect, colour, intake, setup, songs

__all__ = ["collect", "colour", "intake", "setup", "songs"]
