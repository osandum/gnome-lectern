# Translating Lectern

Lectern is a plain `setuptools` package, not a Meson project, so there's
no `i18n.gettext()` module doing this automatically -- the steps below
are the manual equivalent.

## Adding a new language

1. Generate your language's `.po` from the template:

   ```sh
   msginit --input=po/lectern.pot --locale=<lang> --output=po/<lang>.po
   ```

   (`<lang>` is a language code, e.g. `da` for Danish, `pt_BR` for
   Brazilian Portuguese.)

2. Translate the `msgstr ""` entries in `po/<lang>.po`. Every string
   carries a `#:` comment pointing at the source file/line it came
   from, and `#, python-brace-format` marks the ones with a `{name}`
   placeholder — keep those placeholders in the translated string
   (reordering them is fine; `str.format()` doesn't care about order).

3. Add `<lang>` to `po/LINGUAS` (one code per line).

4. Compile and install it where `lectern/i18n.py` looks for it:

   ```sh
   mkdir -p lectern/locale/<lang>/LC_MESSAGES
   msgfmt po/<lang>.po --output-file lectern/locale/<lang>/LC_MESSAGES/lectern.mo
   ```

   `pyproject.toml`'s `package-data` picks up anything installed this
   way, so a normal `pip install .` afterwards ships it.

5. Run the app with your language forced, to check it (GTK reads the
   usual `LANGUAGE`/`LC_ALL`/`LANG` environment variables):

   ```sh
   LANGUAGE=<lang> lectern
   ```

## Updating an existing translation

`msgmerge` folds newly-added/changed source strings into an existing
`.po` without discarding what's already translated:

```sh
msgmerge --update po/<lang>.po po/lectern.pot
```

## Regenerating `po/lectern.pot` (maintainers)

Needed whenever a translatable string changes in one of the files
listed in `po/POTFILES.in`:

```sh
xgettext --from-code=UTF-8 --language=Python \
    --keyword=_ --keyword=ngettext:1,2 \
    --package-name=lectern --package-version="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml","rb"))["project"]["version"])')" \
    --msgid-bugs-address=https://github.com/osandum/lectern/issues \
    --copyright-holder="Ole Sandum" \
    --output=po/lectern.pot --files-from=po/POTFILES.in
```

Then `msgmerge --update` every existing `po/<lang>.po` against the
refreshed template so translators see what's new/changed rather than
starting over.

## Marking a string as translatable

`from .i18n import _` (and `ngettext` too, for a string whose wording
depends on a count) at the top of the module, then wrap the string
literal itself: `_("Some text")`. A string with a runtime value in it
uses a named placeholder rather than an f-string, since the message
catalogue has to hold the template, not the already-interpolated
result: `_("Some text about {thing}").format(thing=thing)`.

See `lectern/i18n.py` for the runtime side (why it's a shared module
rather than each file calling `gettext.translation()` on its own, and
why `_`/`ngettext` are explicit imports rather than the more common
`gettext.install()`, which works by injecting `_` into `__builtins__`).
