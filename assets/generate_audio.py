"""Regenerate every original Asterion sound; no downloads or third-party modules.

Run from any directory: ``python3 assets/generate_audio.py``.
All tones, rhythms and timbres below were created for this project.  No samples,
recorded instruments, pretrained music systems or existing melodies are used.
"""

from array import array
from pathlib import Path
import math
import random
import sys
import wave

RATE = 22050
TAU = math.tau
OUT = Path(__file__).resolve().parent / "audio"


def sine(frequency, t):
    return math.sin(TAU * frequency * t)


def envelope(t, duration, attack=0.012, release=0.1):
    return min(1.0, t / attack, max(0.0, (duration - t) / release))


def write(name, duration, sample, loop=False):
    data = array("h")
    for i in range(round(duration * RATE)):
        t = i / RATE
        value = sample(t)
        if not loop:
            value *= envelope(t, duration, .006, .075)
        data.append(round(max(-0.96, min(0.96, value)) * 32767))
    if sys.byteorder != "little":
        data.byteswap()
    with wave.open(str(OUT / (name + ".wav")), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())
    print(f"{name}.wav  {duration:g}s")


def chime(t, note, delay, strength=1.0):
    u = t - delay
    if u < 0:
        return 0.0
    rise = min(1.0, u / 0.012)
    decay = math.exp(-u * 4.2)
    return strength * rise * decay * (sine(note, u) + .24 * sine(note * 2, u))


def make_sounds():
    OUT.mkdir(parents=True, exist_ok=True)
    randomizer = random.Random(73129)
    # Finite phase-modulated partials create a seamless non-sampled air texture.
    air_partials = [(randomizer.randint(600, 11000) / 8,
                     randomizer.random() * TAU,
                     randomizer.uniform(.4, 1.0)) for _ in range(12)]

    def surface(t):
        breeze = sum(a * math.sin(TAU * f * t + p + .2 * sine(.125, t))
                     for f, p, a in air_partials) / 25
        # Frequencies are integer multiples of 1/16 so the loop closes exactly.
        pad = .17 * sine(130.8125, t) + .10 * sine(196.0, t) + .07 * sine(293.6875, t)
        return breeze * (.65 + .25 * sine(.125, t)) + pad * (.8 + .2 * sine(.0625, t))

    def space(t):
        # An original open voicing, gently changing its overtones across 24 s.
        notes = (65.4166666667, 130.8333333333, 196.0, 261.625, 392.0)
        value = 0.0
        for n, freq in enumerate(notes):
            strength = (.25 / (1 + .7 * n)) * (.72 + .28 * sine((n + 1) / 24, t))
            value += strength * math.sin(TAU * freq * t + .12 * sine(1 / 24, t))
        return value

    def engine(t):
        return (.23 * sine(48, t) + .12 * sine(96, t) + .08 * sine(144, t)
                + .035 * math.sin(TAU * 385 * t + 1.1 * sine(3, t))) * (.9 + .1 * sine(2, t))

    write("surface", 16, surface, loop=True)
    write("space", 24, space, loop=True)
    write("engine", 4, engine, loop=True)
    write("ui", .09, lambda t: .23 * sine(720, t) * envelope(t, .09, .006, .07))
    write("scan", .66, lambda t: (.28 * math.sin(TAU * (350 * t + 550 * t * t))
                                 + .1 * sine(1176, t)) * envelope(t, .66, .03, .22))
    write("mine", .16, lambda t: (.34 * sine(118, t) + .16 * sine(367, t)
                                 + .08 * sine(1737, t)) * math.exp(-18 * t) * envelope(t, .16, .004, .03))
    write("collect", .55, lambda t: .28 * (chime(t, 659.25, 0) + chime(t, 987.77, .10)))
    write("craft", 1.25, lambda t: .24 * (chime(t, 440, 0) + chime(t, 554.37, .11)
                                        + chime(t, 739.99, .24) + chime(t, 1108.73, .37)))
    write("discover", 1.8, lambda t: .23 * (chime(t, 392, 0) + chime(t, 587.33, .22)
                                          + chime(t, 783.99, .44) + chime(t, 1174.66, .66)))
    write("alert", .62, lambda t: .24 * (sine(554.37, t) + .25 * sine(1108.73, t))
                                      * envelope(t, .62, .018, .15)
                                      * (.3 + .7 * max(0, sine(4.8, t))))
    write("launch", 1.65, lambda t: (.24 * math.sin(TAU * (45 * t + 25 * t * t))
                                     + .12 * math.sin(TAU * (91 * t + 50 * t * t))
                                     + .06 * sine(533, t)) * envelope(t, 1.65, .24, .55))
    write("land", .95, lambda t: (.27 * math.sin(TAU * (95 * t - 28 * t * t))
                                  + .13 * sine(51, t)) * envelope(t, .95, .024, .7))
    write("warp", 2.6, lambda t: (.21 * math.sin(TAU * (76 * t + 62 * t * t))
                                  + .12 * math.sin(TAU * (114 * t + 93 * t * t))
                                  + .06 * sine(784, t)) * envelope(t, 2.6, .42, .85)
                                  * (.72 + .28 * sine(8 - t, t)))


if __name__ == "__main__":
    make_sounds()
