class HostError(Exception):
    """Host-side failure; never converted into a guest success value."""


class NotFound(HostError):
    """A looked-up path component does not exist; not an I/O failure."""


class UnimplementedImport(HostError):
    def __init__(self, name: str):
        super().__init__(f"unimplemented import: {name}")
        self.name = name


class PluginError(HostError):
    """The plug-in violated the request protocol or reported a fatal result."""
