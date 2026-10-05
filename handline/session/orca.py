"""The old name of `handline.session.term`. `import handline.session.orca` still works for a release and
gives the very module `term` (one object, not a copy), so state is not doubled."""
import sys

from . import term

sys.modules[__name__] = term
