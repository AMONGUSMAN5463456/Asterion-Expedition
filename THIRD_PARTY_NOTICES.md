# Third-party notices

## Bundled fonts

`assets/fonts/DejaVuSans.ttf` and `assets/fonts/DejaVuSans-Bold.ttf` are unmodified
DejaVu fonts. Their copyright and redistributable Bitstream Vera / DejaVu terms
are included in `assets/fonts/LICENSE-DejaVu.txt`. They remain available as the
interface's fallback fonts.

`assets/fonts/Barlow-Regular.ttf`, `Barlow-SemiBold.ttf`, and `Barlow-Light.ttf`
are unmodified Barlow fonts, copyright 2017 The Barlow Project Authors. The
interface's instrument numerals use the unmodified `Rajdhani-Medium.ttf`,
copyright 2014 Indian Type Foundry. Both families are distributed under the
SIL Open Font License 1.1; the complete notices are included in
`assets/fonts/LICENSE-Barlow.txt` and `assets/fonts/LICENSE-Rajdhani.txt`.
The font files were obtained from the [Google Fonts repository](https://github.com/google/fonts),
with upstream projects at [Barlow](https://github.com/jpt/barlow) and
[Rajdhani](https://github.com/itfoundry/rajdhani). The bundled font families are
the only third-party art assets in this source package.

## Runtime dependency

Panda3D 1.10.16 is installed separately into `.venv` by the launcher from the
official Python Package Index. Its license and third-party notices are part of
that installed distribution. Panda3D is developed by the Panda3D contributors
and is distributed under its Modified BSD license. No Panda3D binary is included
in the source ZIP. See https://www.panda3d.org/license/ for upstream terms.

Python is installed separately by the player and has its own license. It is not
included in this package.

## Original project assets

World geometry, interface composition, ship design, prose, names, and the audio
synthesis code were created for Asterion Expedition. The WAV files are direct
output of `assets/generate_audio.py`, which uses mathematical synthesis and no
external samples or existing music. The original code and assets are distributed
under the project MIT license in `LICENSE`.

This is an independent original game. It contains no assets, source code,
characters, logos, story text, recorded music, or sound samples copied from a
commercial space-exploration game. No affiliation or endorsement is implied.
