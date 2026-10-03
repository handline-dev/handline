"""The old name of the `handline` package. `import ocp.db` still works for a release and
gives the very module `handline.db` (one object, not a copy), so state is not doubled."""
import importlib
import importlib.abc
import importlib.util
import sys

import handline


class _Alias(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, name, path=None, target=None):
        if name.startswith("ocp."):
            return importlib.util.spec_from_loader(name, self)

    def create_module(self, spec):
        return importlib.import_module("handline" + spec.name[len("ocp"):])

    def exec_module(self, module):
        pass

    def get_code(self, fullname):
        """`python -m ocp.<module>` (runpy) asks the loader for the code."""
        return importlib.util.find_spec("handline" + fullname[len("ocp"):]).loader.get_code("handline" + fullname[len("ocp"):])


sys.meta_path.insert(0, _Alias())
sys.modules[__name__] = handline
