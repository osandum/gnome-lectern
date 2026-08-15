"""Gettext setup, in one place so every module doing `from .i18n import
_` shares one translation catalogue, rather than each calling
gettext.translation() separately (redundant .mo lookups scattered
around) or gettext.install() (which works by injecting `_` into
__builtins__ -- an implicit global a reader has to already know about
to explain where `_` came from, which this codebase avoids everywhere
else in favour of explicit imports).

lectern/locale/<lang>/LC_MESSAGES/lectern.mo is where a built
translation lives (see pyproject.toml's package-data and po/README.md
for how one gets there from po/lectern.pot). Running from an editable
install with none built yet -- the common case during development --
falls back to NullTranslations: every _()/ngettext() call just hands
back its argument unchanged, so untranslated strings are the default,
not an error.
"""
import gettext
import os

DOMAIN = "lectern"
LOCALE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locale")

_translation = gettext.translation(DOMAIN, LOCALE_DIR, fallback=True)
_ = _translation.gettext
ngettext = _translation.ngettext
