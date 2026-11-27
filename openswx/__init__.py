"""openswx: open-source parametric CAD with a SOLIDWORKS-style workflow and a built-in MCP server."""

__version__ = "0.4.2"


def _defer_sklearn() -> None:
    """build123d imports scikit-learn (DBSCAN, for STL primitive detection, which openswx never uses) when it
    loads: 1-2 s of every start. Until something actually uses it, stand-in modules take its place; the first
    use of one swaps the real package in."""
    import sys
    import types
    if "sklearn" in sys.modules:
        return

    def real():
        for name in ("sklearn.cluster", "sklearn"):
            if getattr(sys.modules.get(name), "_openswx_stub", False):
                del sys.modules[name]
        import sklearn.cluster
        return sklearn.cluster

    class _Stub(types.ModuleType):
        _openswx_stub = True

        def __getattr__(self, attr):
            if attr.startswith("__"):  # the import machinery probes these (__path__, __spec__ ...)
                raise AttributeError(attr)
            mod = real()
            return getattr(mod if self.__name__ == "sklearn.cluster" else sys.modules["sklearn"], attr)

    def DBSCAN(*args, **kwargs):  # noqa: N802 - the class it stands in for
        return real().DBSCAN(*args, **kwargs)

    pkg, cluster = _Stub("sklearn"), _Stub("sklearn.cluster")
    pkg.__path__ = []
    pkg.cluster = cluster
    cluster.DBSCAN = DBSCAN
    sys.modules["sklearn"], sys.modules["sklearn.cluster"] = pkg, cluster


_defer_sklearn()
