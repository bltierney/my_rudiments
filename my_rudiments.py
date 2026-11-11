#!/usr/bin/env python3
"""Drum rudiment practice generator.

Reads rudiment patterns from a text file (rudiments.txt by default) and makes:
  * an MP3/WAV practice track: a spoken name, a count-in, then each example,
  * a PDF practice sheet with the notation and sticking (--sheet),
  * a video of the notation with a cursor that follows the audio (--video).

The notation is drawn with Verovio (pip install verovio); the PDF and video use
PyMuPDF (pip install pymupdf) and Pillow. See rudiments.txt for the pattern format.
"""
import math, os, re, subprocess, sys, warnings
from fractions import Fraction
import numpy as np
from scipy.io import wavfile
warnings.filterwarnings("ignore", category=wavfile.WavFileWarning)  # macOS 'say' adds extra WAV chunks
warnings.filterwarnings("ignore", category=FutureWarning)

SR = 44100
DEFAULT_BPM = 70
BPM_OVERRIDE = None                # --bpm on the command line beats any bpm in the file

def tempo_of(ex):
    return BPM_OVERRIDE or ex.bpm or DEFAULT_BPM
ACC, TAP, GRACE = 1.0, 0.15, 0.08  # accent, normal stroke, grace note (lower TAP = bigger accent contrast)
VOICE = None                       # macOS voice name for 'say' (see: say -v '?')
PRONOUNCE = {}                     # word -> spelling for the voice ('pronounce:' lines in the file)

def spoken(text):
    """Respell words the voice gets wrong; on-screen text keeps the real spelling."""
    for word, say_as in PRONOUNCE.items():
        text = re.sub(rf"\b{re.escape(word)}\b", say_as, text, flags=re.I)
    return text
rng = np.random.default_rng(7)

def opp(h):
    return "L" if h == "R" else "R"

# ======================= reading rudiments.txt =======================
class Example:
    def __init__(self, name, number=None, levels=(1,), time=4, pattern="", where="", bpm=None,
                 book=None):
        self.name, self.number, self.levels, self.bpm = name, number, tuple(levels), bpm
        self.book = book or {}             # optional 'pdf:' override (page/lines/part/skip)
        self.beats_per_bar, self.where = time, where
        self.notes, self.bars, self.repeats = parse_pattern(pattern, time, where)
        self.length = self.bars * time                       # written length in beats
        self.played = expand_repeats(self.notes, self.repeats, self.length)

    @property
    def label(self):
        lv = sorted(self.levels)
        lvl = (f"Level {lv[0]}" if len(lv) == 1 else
               "Levels " + ", ".join(map(str, lv[:-1])) + f" and {lv[-1]}")
        num = f"Number {self.number}. " if self.number is not None else ""
        return f"{num}{self.name}. {lvl}."

    @property
    def group(self):
        return self.number if self.number is not None else self.name

def load_rudiments(path):
    examples, cur, key, file_bpm = [], None, None, None
    def read_bpm(v):
        try:
            b = float(v)
            assert 10 <= b <= 400
            return b
        except (ValueError, AssertionError):
            raise ValueError(f"bpm must be a number between 10 and 400, got {v!r}")
    def read_book(v):
        """Optional override of where an example is in the book PDF, e.g.
        'page 3, line 2', 'page 10, lines 4-5', 'page 9, line 5, part 2', 'skip 13,30'."""
        out, parts = {}, [p.strip() for p in v.split(",")]
        i = 0
        while i < len(parts):
            p = parts[i].lower()
            if m := re.fullmatch(r"page\s+(\d+)", p):
                out["page"] = int(m.group(1))
            elif m := re.fullmatch(r"lines?\s+(\d+)(?:\s*-\s*(\d+))?", p):
                a = int(m.group(1)); out["lines"] = list(range(a - 1, int(m.group(2) or a)))
            elif m := re.fullmatch(r"part\s+(\d+)", p):
                out["part"] = int(m.group(1)) - 1
            elif m := re.fullmatch(r"skip\s+(\d+)", p):
                skip = {int(m.group(1)) - 1}
                while i + 1 < len(parts) and parts[i + 1].isdigit():   # 'skip 13,30'
                    i += 1; skip.add(int(parts[i]) - 1)
                out["skip"] = skip
            else:
                raise ValueError(f"can't read {parts[i]!r} in 'pdf:' (expected e.g. 'page 3, line 2', "
                                 "'part 2' or 'skip 13,30')")
            i += 1
        if ("page" in out) != ("lines" in out):
            raise ValueError("'pdf:' needs both a page and a line, e.g. 'page 3, line 2'")
        return out
    def finish():
        if cur is None:
            return
        try:
            lv = [int(x) for x in str(cur.get("level", "1")).replace(" ", "").split(",") if x]
            t = cur.get("time", "4/4").strip()
            m = re.fullmatch(r"(\d+)\s*/\s*4", t)
            if not m:
                raise ValueError(f"time must be N/4, got {t!r}")
            bpm = read_bpm(cur["bpm"]) if cur.get("bpm") else file_bpm
            book = read_book(cur["pdf"]) if cur.get("pdf") else None
            examples.append(Example(cur["name"], int(cur["number"]) if cur.get("number") else None,
                                    lv, int(m.group(1)), cur.get("pattern", ""), cur["where"], bpm,
                                    book))
        except ValueError as e:
            sys.exit(f"{cur['where']} [{cur['name']}]: {e}")
    with open(path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            m = re.fullmatch(r"\s*\[(.+)\]\s*", line)
            if m:
                finish()
                cur, key = {"name": m.group(1).strip(), "where": f"{path}:{n}"}, None
                continue
            if cur is None:
                m = re.fullmatch(r"\s*pronounce\s*:\s*(.+?)\s*=\s*(.+?)\s*", line, re.I)
                if m:                                  # 'pronounce: ratamacue = rat-uh-muh-cue'
                    PRONOUNCE[m.group(1).lower()] = m.group(2)
                    continue
                m = re.fullmatch(r"\s*bpm\s*:\s*(.*)", line, re.I)
                if not m:
                    sys.exit(f"{path}:{n}: expected a [Name] line, or a file-wide 'bpm:' or "
                             "'pronounce:' line, first")
                try:
                    file_bpm = read_bpm(m.group(1))
                except ValueError as e:
                    sys.exit(f"{path}:{n}: {e}")
                continue
            m = re.fullmatch(r"([A-Za-z]+)\s*:\s*(.*)", line.strip())
            if m and not raw[:1].isspace():
                key = m.group(1).lower()
                if key not in ("number", "level", "levels", "time", "bpm", "pdf", "pattern"):
                    sys.exit(f"{path}:{n}: unknown key {key!r}")
                key = "level" if key == "levels" else key
                cur[key] = m.group(2)
            elif key == "pattern":
                cur["pattern"] += " " + line.strip()
            else:
                sys.exit(f"{path}:{n}: can't read this line: {line.strip()!r}")
    finish()
    return examples

TOK = re.compile(r"^([fg]?)([RL])([~z]?)(>?)$")

def parse_pattern(text, beats_per_bar, where=""):
    """-> written notes (onset and length in beats, as Fractions), bar count, repeat spans."""
    sub, t, notes = Fraction(4), Fraction(0), []
    repeats, rep_start = [], None
    for tok in text.split():
        if tok.startswith("@"):
            try:
                sub = Fraction(tok[1:])
                assert sub > 0
            except (ValueError, AssertionError):
                raise ValueError(f"bad note size {tok!r} (use @1, @2, @3, @4, @6, @8 ...)")
            continue
        if tok == "|":
            if t % beats_per_bar:
                raise ValueError(f"bar line at beat {float(t):g} doesn't fall on a bar boundary")
            continue
        if tok == "|:":
            rep_start = t
            continue
        if tok == ":|":
            repeats.append((rep_start or Fraction(0), t))
            rep_start = None
            continue
        d = 1 / sub
        if tok == ".":
            notes.append(dict(t=t, d=d, rest=True))
            t += d
            continue
        m = TOK.match(tok)
        if not m:
            raise ValueError(f"can't read {tok!r} in the pattern")
        orn, hand, mod, acc = m.groups()
        notes.append(dict(t=t, d=d, hand=hand, orn=orn, mod=mod, acc=bool(acc), rest=False))
        t += d
    if not notes:
        raise ValueError("empty pattern")
    if t % beats_per_bar:
        raise ValueError(f"pattern is {float(t):g} beats long, which isn't a whole number of "
                         f"{beats_per_bar}-beat bars")
    for a, b in repeats:
        if a % beats_per_bar or b % beats_per_bar:
            raise ValueError("repeat signs must be on bar lines")
    for i, n in enumerate(notes):
        n["i"] = i
    return notes, int(t // beats_per_bar), repeats

def expand_repeats(notes, repeats, length):
    """Played order: list of (written note, played onset in beats)."""
    spans, pos = [], Fraction(0)
    for a, b in sorted(repeats):
        if a > pos:
            spans.append((pos, a))
        spans += [(a, b), (a, b)]
        pos = b
    if pos < length:
        spans.append((pos, Fraction(length)))
    out, t0 = [], Fraction(0)
    for a, b in spans:
        for n in notes:
            if a <= n["t"] < b and not n["rest"]:
                out.append((n, t0 + n["t"] - a))
        t0 += b - a
    return out, t0

# ======================= audio =======================
def make_snare(n=int(0.35 * SR)):
    tt = np.arange(n) / SR
    body = (np.sin(2 * np.pi * 185 * tt) * np.exp(-tt * 38) * 0.6 +
            np.sin(2 * np.pi * 330 * tt) * np.exp(-tt * 55) * 0.25)
    noise = rng.standard_normal(n)
    noise = np.convolve(np.diff(noise, prepend=0), np.ones(3) / 3, "same")
    wires = noise * np.exp(-tt * 22) * 0.55
    stick = rng.standard_normal(n) * np.exp(-tt * 400) * 0.5
    s = body + wires + stick
    return s / np.max(np.abs(s))

SNARES = [make_snare() for _ in range(6)]  # variants to avoid a machine-gun effect

def make_click(freq):
    n = int(0.03 * SR); tt = np.arange(n) / SR
    return np.sin(2 * np.pi * freq * tt) * np.exp(-tt * 180)
CLICK_HI, CLICK_LO = make_click(1800), make_click(1200)

def place(buf, sample, t_sec, gain, pan):
    i = int(round(t_sec * SR))
    if i < 0: return
    j = min(len(buf), i + len(sample))
    if j <= i: return
    s = sample[: j - i] * gain
    buf[i:j, 0] += s * (1 - pan) ** 0.5
    buf[i:j, 1] += s * (1 + pan) ** 0.5

def hit(buf, t, vel, hand, sustain_damp=False):
    pan = 0.35 if hand == "R" else -0.35
    smp = SNARES[rng.integers(len(SNARES))]
    if sustain_damp:
        smp = smp * np.exp(-np.arange(len(smp)) / SR * 30)
    place(buf, smp, t, vel * rng.uniform(0.95, 1.05), pan)

def render_notes(buf, played, t0, beat):
    for n, pt in played:
        t = t0 + float(pt) * beat
        d = float(n["d"]) * beat
        v = ACC if n["acc"] else TAP
        h = n["hand"]
        if n["orn"] == "f":
            hit(buf, t - 0.028, GRACE, opp(h))
        elif n["orn"] == "g":
            hit(buf, t - 0.075, GRACE, opp(h))
            hit(buf, t - 0.038, GRACE * 0.9, opp(h))
        if n["mod"] == "~":
            hit(buf, t, v, h)
            hit(buf, t + d / 2, TAP * 0.85, h)
        elif n["mod"] == "z":
            k, bt = 0, t
            while bt < t + d - 0.01:
                hit(buf, bt, v * 0.75 * (0.8 ** k), h, sustain_damp=True)
                k += 1; bt += 0.022
        else:
            hit(buf, t, v, h)

def tts(text, path):
    # Try voices in order: macOS 'say', espeak-ng MBROLA voice, plain espeak-ng.
    attempts = [
        ["say", "-r", "170"] + (["-v", VOICE] if VOICE else []) +
        ["-o", path, "--data-format=LEI16@22050", text],
        ["espeak-ng", "-v", "mb-us2", "-s", "150", "-w", path, text],
        ["espeak-ng", "-v", "en-us", "-s", "150", "-w", path, text],
        ["espeak", "-v", "en-us", "-s", "150", "-w", path, text],
    ]
    for cmd in attempts:
        try:
            r = subprocess.run(cmd, capture_output=True)
            if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 1000:
                break
        except FileNotFoundError:
            continue
    else:
        sys.exit("No text-to-speech found. Install espeak-ng (Linux: apt install espeak-ng, "
                 "macOS has 'say' built in).")
    sr, x = wavfile.read(path)
    x = x.astype(np.float64) / 32768.0
    if x.ndim > 1: x = x.mean(axis=1)
    if sr != SR:
        from scipy.signal import resample_poly
        g = math.gcd(sr, SR); x = resample_poly(x, SR // g, sr // g)
    return x / (np.max(np.abs(x)) + 1e-9) * 0.8

# ======================= choosing and grouping examples =======================
def select(examples, levels, only):
    out = []
    for ex in examples:
        if not set(ex.levels) & levels:
            continue
        if only:
            ok = False
            for item in only:
                if item.isdigit():
                    ok |= ex.number == int(item)
                else:
                    ok |= item.lower() in ex.name.lower()
            if not ok:
                continue
        out.append(ex)
    return out

def make_chains(examples, seamless):
    """Takes: lists of examples played as one take (one spoken name and count-in,
    then the examples back to back with no gap)."""
    chains = []
    for ex in examples:
        if seamless and chains:
            prev = chains[-1][-1]
            if prev.group == ex.group and min(ex.levels) >= max(prev.levels):
                chains[-1].append(ex)
                continue
        chains.append([ex])
    return chains

def chain_label(chain, levels):
    if len(chain) == 1:
        return chain[0].label
    lv = sorted(set().union(*(set(e.levels) for e in chain)) & levels)
    words = ", ".join(map(str, lv[:-1])) + " and " + str(lv[-1])
    num = f"Number {chain[0].number}. " if chain[0].number is not None else ""
    return f"{num}{chain[0].name}. Levels {words}."

def render_audio(chains, levels, passes, click, tmpdir):
    tmp = os.path.join(tmpdir, "_tts.wav")
    segs, timeline, pos = [], [], 0
    for chain in chains:
        label = chain_label(chain, levels)
        parts = []
        for ex in chain:
            played, plen = ex.played
            parts.append(dict(ex=ex, played=played, length=float(plen), bpm=tempo_of(ex),
                              beat=60.0 / tempo_of(ex)))
        beat0 = parts[0]["beat"]                 # count-in at the first example's tempo
        bar = 4 * beat0
        play_len = passes * sum(p["length"] * p["beat"] for p in parts)
        voice = tts(spoken(label), tmp)
        total = len(voice) / SR + 0.5 + bar + play_len + 1.5
        buf = np.zeros((int(total * SR) + SR, 2))
        buf[: len(voice), 0] += voice; buf[: len(voice), 1] += voice
        t = len(voice) / SR + 0.5
        for b in range(4):  # count-in bar
            place(buf, CLICK_HI if b == 0 else CLICK_LO, t + b * beat0, 0.35, 0)
        start = t + bar
        ps = start
        for part in parts:                      # levels follow each other with no gap
            part["start"] = pos / SR + ps
            bpb, beat = part["ex"].beats_per_bar, part["beat"]
            for _ in range(passes):
                render_notes(buf, part["played"], ps, beat)
                if click:
                    for b in range(int(round(part["length"]))):
                        place(buf, CLICK_HI if b % bpb == 0 else CLICK_LO, ps + b * beat, 0.12, 0)
                ps += part["length"] * beat
        segs.append(buf[: int(total * SR)])
        timeline.append(dict(label=label, parts=parts, count=pos / SR + t, play=pos / SR + start,
                             count_beat=beat0,
                             end=(pos + int(total * SR)) / SR, passes=passes))
        pos += int(total * SR)
        print(f"{label:70s} {passes * sum(p['length'] for p in parts) / 4:.0f} bars", file=sys.stderr)
    if os.path.exists(tmp):
        os.remove(tmp)
    audio = np.concatenate(segs)
    audio /= np.max(np.abs(audio)) * 1.12
    print(f"{len(timeline)} takes, {len(audio) / SR / 60:.1f} min", file=sys.stderr)
    return (audio * 32767).astype(np.int16), timeline

def render_audio_continuous(chains, levels, passes, click, tmpdir):
    """Everything back to back: one count-in at the start, then each take straight after
    the previous one, with its name spoken over its first bars (drums dip under the voice)."""
    tmp = os.path.join(tmpdir, "_tts.wav")
    takes = []
    for chain in chains:
        parts = []
        for ex in chain:
            played, plen = ex.played
            parts.append(dict(ex=ex, played=played, length=float(plen), bpm=tempo_of(ex),
                              beat=60.0 / tempo_of(ex)))
        takes.append((chain_label(chain, levels), parts))
    beat0 = takes[0][1][0]["beat"]
    count = 0.5                                   # short lead-in, then one count-in bar
    play0 = count + 4 * beat0
    total = play0 + sum(passes * p["length"] * p["beat"] for _, ps in takes for p in ps) + 2.0
    drums = np.zeros((int(total * SR) + SR, 2))
    voice = np.zeros(len(drums))
    for b in range(4):
        place(drums, CLICK_HI if b == 0 else CLICK_LO, count + b * beat0, 0.35, 0)
    timeline, ps = [], play0
    for n, (label, parts) in enumerate(takes):
        take_start = ps
        v = tts(spoken(label), tmp)
        i = int(round(take_start * SR))
        voice[i:i + len(v)] += v[: len(voice) - i]
        for part in parts:
            part["start"] = ps
            bpb, beat = part["ex"].beats_per_bar, part["beat"]
            for _ in range(passes):
                render_notes(drums, part["played"], ps, beat)
                if click:
                    for b in range(int(round(part["length"]))):
                        place(drums, CLICK_HI if b % bpb == 0 else CLICK_LO, ps + b * beat, 0.12, 0)
                ps += part["length"] * beat
        timeline.append(dict(label=label, parts=parts, passes=passes,
                             count=count if n == 0 else take_start, count_beat=beat0,
                             play=take_start, end=ps))
        print(f"{label:70s} {passes * sum(p['length'] for p in parts) / 4:.0f} bars", file=sys.stderr)
    timeline[-1]["end"] = total
    if os.path.exists(tmp):
        os.remove(tmp)
    # duck the drums while the voice speaks, so the name stays clear
    k = int(0.08 * SR)
    env = np.convolve(np.abs(voice), np.ones(k) / k, "same")
    duck = 1.0 - 0.55 * np.clip(env / 0.05, 0, 1)
    dmax = np.max(np.abs(drums)) + 1e-9
    audio = drums / dmax * 0.75 * duck[:, None] + voice[:, None] * 0.95
    audio = audio[: int(total * SR)]
    audio /= np.max(np.abs(audio)) * 1.12
    print(f"{len(timeline)} takes, {len(audio) / SR / 60:.1f} min, no pauses", file=sys.stderr)
    return (audio * 32767).astype(np.int16), timeline

def write_audio(pcm, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    if out.lower().endswith(".mp3"):
        wav_tmp = out[:-4] + ".tmp.wav"
        wavfile.write(wav_tmp, SR, pcm)
        try:
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", wav_tmp,
                            "-codec:a", "libmp3lame", "-b:a", "192k", out], check=True)
        except FileNotFoundError:
            sys.exit(f"ffmpeg not found; WAV left at {wav_tmp}. "
                     "Install it (brew install ffmpeg / apt install ffmpeg) for MP3 output.")
        os.remove(wav_tmp)
    else:
        wavfile.write(out, SR, pcm)
    print(f"wrote {out}", file=sys.stderr)

# ======================= notation (MEI for Verovio) =======================
def _is_pow2(x):
    return x.denominator == 1 and x.numerator & (x.numerator - 1) == 0

def _note_value(sub):
    """notes-per-beat -> (MEI dur of the written note, tuplet base notes per beat)."""
    base = 1 << int(math.floor(math.log2(sub))) if sub >= 1 else 1
    if sub < 1:                                  # longer than a beat: half / whole notes
        return {Fraction(1, 2): 2, Fraction(1, 4): 1}.get(sub, 4), sub
    return 4 * base, base

def to_mei(ex, bars_per_line=2):
    """MEI for one example; note ids are 'n<index>' (index into ex.notes)."""
    bpb = ex.beats_per_bar
    rep_starts = {a for a, _ in ex.repeats}
    rep_ends = {b for _, b in ex.repeats}
    measures = []
    for m in range(ex.bars):
        m0, m1 = Fraction(m * bpb), Fraction((m + 1) * bpb)
        ns = [n for n in ex.notes if m0 <= n["t"] < m1]
        layer = _layer(ns)
        attrs = f'n="{m + 1}"'
        if m0 in rep_starts:
            attrs += ' left="rptstart"'
        if m1 in rep_ends:
            attrs += ' right="rptend"'
        elif m == ex.bars - 1:
            attrs += ' right="end"'
        measures.append(f'<measure {attrs}><staff n="1"><layer n="1">{layer}</layer></staff></measure>')
        if (m + 1) % bars_per_line == 0 and m < ex.bars - 1:
            measures.append("<sb/>")             # line break
    body = "".join(measures)
    return ('<?xml version="1.0" encoding="UTF-8"?>'
            '<mei xmlns="http://www.music-encoding.org/ns/mei" meiversion="5.0">'
            '<meiHead><fileDesc><titleStmt><title/></titleStmt><pubStmt/></fileDesc></meiHead>'
            '<music><body><mdiv><score><scoreDef><staffGrp>'
            f'<staffDef n="1" lines="5" meter.count="{bpb}" meter.unit="4"><clef shape="perc"/></staffDef>'
            f'</staffGrp></scoreDef><section>{body}</section></score></mdiv></body></music></mei>')

def _note_xml(n, dur):
    if n["rest"]:
        return f'<rest xml:id="n{n["i"]}" dur="{dur}"/>'
    a = f'<note xml:id="n{n["i"]}" dur="{dur}" pname="c" oct="5" stem.dir="up"'
    if n["mod"] == "~":
        a += ' stem.mod="1slash"'
    elif n["mod"] == "z":
        a += ' stem.mod="z"'
    a += ">"
    if n["acc"]:
        a += '<artic artic="acc" place="above"/>'
    stk = n["hand"] * (2 if n["mod"] == "~" else 1)
    a += f'<verse n="1"><syl>{stk}</syl></verse></note>'
    return a

def _grace_xml(n):
    if n.get("rest") or not n["orn"]:
        return ""
    g = opp(n["hand"])
    if n["orn"] == "f":
        return ('<graceGrp grace="unacc"><note dur="8" pname="c" oct="5" stem.dir="up" '
                'stem.mod="1slash"/></graceGrp>')
    return ('<graceGrp grace="unacc"><beam><note dur="16" pname="c" oct="5" stem.dir="up"/>'
            '<note dur="16" pname="c" oct="5" stem.dir="up"/></beam></graceGrp>')

def _layer(ns):
    """Beam notes until the running time lands on a beat; tuplets per beat / note size."""
    out, group = [], []
    def flush_group():
        if not group:
            return
        items = []
        # tuplets: consecutive notes with the same non-power-of-two size, split at beats
        i = 0
        while i < len(group):
            n = group[i]; sub = 1 / n["d"]
            dur, base = _note_value(sub)
            if _is_pow2(sub) or sub < 1:
                items.append(_grace_xml(n) + _note_xml(n, dur)); i += 1
                continue
            j, tot = i, Fraction(0)
            while j < len(group) and 1 / group[j]["d"] == sub:
                tot += group[j]["d"]; j += 1
                if (group[j - 1]["t"] + group[j - 1]["d"]) % 1 == 0 or tot >= 1:
                    break
            cnt = j - i
            numbase = Fraction(cnt) * base / sub
            nb = int(numbase) if numbase.denominator == 1 else max(1, round(float(numbase)))
            inner = "".join(_grace_xml(m) + _note_xml(m, dur) for m in group[i:j])
            items.append(f'<tuplet num="{cnt}" numbase="{nb}" bracket.visible="false" '
                         f'num.format="count">{inner}</tuplet>')
            i = j
        beamable = [n for n in group if n["d"] < 1]
        if len(group) > 1 and len(beamable) == len(group):
            lead = ""
            first = items[0]
            if first.startswith("<graceGrp"):           # keep a leading grace note outside the beam
                k = first.index("</graceGrp>") + len("</graceGrp>")
                lead, items[0] = first[:k], first[k:]
            elif first.startswith("<tuplet") and "<graceGrp" in first[:first.index(">") + 12]:
                k0 = first.index(">") + 1
                k = first.index("</graceGrp>", k0) + len("</graceGrp>")
                lead, items[0] = first[k0:k], first[:k0] + first[k:]
            out.append(lead + "<beam>" + "".join(items) + "</beam>")
        else:
            out.append("".join(items))
        group.clear()
    for n in ns:
        if n["d"] >= 1:
            flush_group()
            group.append(n); flush_group()
            continue
        group.append(n)
        if (n["t"] + n["d"]) % 1 == 0:
            flush_group()
    flush_group()
    return "".join(out)

# ======================= notation (SVG) =======================
class Engraving:
    """Verovio SVG for one example, cleaned for MuPDF, plus note positions."""
    def __init__(self, ex, page_width=2400, bars_per_line=2):
        import verovio
        verovio.enableLog(verovio.LOG_ERROR)
        tk = verovio.toolkit()
        tk.setOptions({"pageWidth": page_width, "adjustPageHeight": True, "breaks": "encoded",
                       "header": "none", "footer": "none", "scale": 40, "lyricSize": 3.6,
                       "pageMarginLeft": 60, "pageMarginRight": 60, "pageMarginTop": 40,
                       "pageMarginBottom": 40, "spacingSystem": 8, "noJustification": False,
                       "spacingLinear": 0.3, "spacingNonLinear": 0.6, "svgViewBox": False,
                       "minLastJustification": 0.0})
        if not tk.loadData(to_mei(ex, bars_per_line)):
            raise RuntimeError(f"Verovio could not read the notation for {ex.label}")
        if tk.getPageCount() > 1:
            print(f"note: {ex.label} spans {tk.getPageCount()} pages; only page 1 is shown",
                  file=sys.stderr)
        svg = tk.renderToSVG(1)
        outer = re.search(r'<svg width="([\d.]+)px" height="([\d.]+)px"[^>]*>', svg)
        inner = re.search(r'<svg class="definition-scale"[^>]*viewBox="([^"]+)"[^>]*>', svg)
        self.w, self.h = float(outer.group(1)), float(outer.group(2))
        vb = [float(v) for v in inner.group(1).split()]
        k = self.w / vb[2]
        body = svg[inner.end():]
        body = body[:body.rindex("</svg>")]
        body = body[:body.rindex("</svg>")]
        clean = (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
                 f'width="{self.w}" height="{self.h}" viewBox="{inner.group(1)}" fill="black" '
                 f'font-family="Times, serif">' + svg[outer.end():inner.start()] + body + "</svg>")
        clean = clean.replace("<path ", '<path stroke="black" ')   # lines/stems need a stroke; text must not get one
        clean = re.sub(r"<style.*?</style>", "", clean, flags=re.S)
        clean = re.sub(r'<g [^>]*class="[^"]*placeholder[^"]*"[^>]*>.*?</g>', "", clean, flags=re.S)
        self.svg = clean
        # page margin offset, then each written note's notehead centre (in output px)
        mm = re.search(r'class="page-margin" transform="translate\(([-\d.]+),\s*([-\d.]+)\)', svg)
        ox, oy = float(mm.group(1)), float(mm.group(2))
        self.pos = {}
        for m in re.finditer(r'<g id="n(\d+)" class="(note|rest)"', svg):
            # the first glyph after a note's start is its notehead (or the rest symbol)
            k0 = svg.find('class="notehead"', m.end()) if m.group(2) == "note" else m.end()
            u = re.compile(r'translate\(([-\d.]+),\s*([-\d.]+)\)\s*scale\(([-\d.]+)').search(svg, k0)
            if u:
                x, y, s = float(u.group(1)), float(u.group(2)), float(u.group(3))
                self.pos[int(m.group(1))] = ((ox + x + 157 * s) * k, (oy + y) * k)
        # staff lines -> vertical band of each system, in px
        self.staff_space = 180 * k
        missing = [n["i"] for n in ex.notes if not n["rest"] and n["i"] not in self.pos]
        if missing:
            raise RuntimeError(f"{ex.label}: couldn't find {len(missing)} notes in the engraving")

    def pdf_page(self):
        import fitz
        svgdoc = fitz.open("svg", self.svg.encode())
        return fitz.open("pdf", svgdoc.convert_to_pdf())

    def raster(self, zoom):
        import fitz
        doc = fitz.open("svg", self.svg.encode())
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        return np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w, pix.n)[:, :, :3].copy()

# ======================= PDF practice sheet =======================
def write_sheet(examples, out, title):
    import fitz
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    W_, H_, M = 612, 792, 48                     # US letter, points
    doc = fitz.open()
    page, y = None, H_
    def new_page():
        nonlocal page, y
        page = doc.new_page(width=W_, height=H_)
        y = M
        page.insert_text((W_ - M, H_ - 24), f"{doc.page_count}", fontsize=9, fontname="helv",
                         color=(0.5, 0.5, 0.5))
    for k, ex in enumerate(examples):
        eng = Engraving(ex, page_width=1700)
        scale = min((W_ - 2 * M) / eng.w, 6.0 / eng.staff_space)   # ~6 pt between staff lines
        h = eng.h * scale
        need = 22 + h + 14
        if page is None or y + need > H_ - M:
            new_page()
            if k == 0:
                page.insert_text((M, y + 16), title, fontsize=20, fontname="hebo")
                y += 38
        page.insert_text((M, y + 13), ex.label.rstrip("."), fontsize=12, fontname="hebo")
        bt = f"{tempo_of(ex):g} bpm"
        page.insert_text((W_ - M - fitz.get_text_length(bt, "helv", 10), y + 13), bt,
                         fontsize=10, fontname="helv", color=(0.35, 0.35, 0.35))
        y += 20
        src = eng.pdf_page()
        page.show_pdf_page(fitz.Rect(M, y, M + eng.w * scale, y + h), src, 0)
        y += h + 14
        print(f"sheet {k + 1}/{len(examples)}", file=sys.stderr, end="\r")
    doc.save(out, garbage=3, deflate=True)
    print(f"\nwrote {out} ({doc.page_count} pages)", file=sys.stderr)

# ======================= video =======================
W, H, FPS = 1920, 1080, 30
BG = (250, 248, 243)
CURSOR = np.array([230, 90, 40], np.float32)

def _font(size, bold=False):
    from PIL import ImageFont
    cands = (["/System/Library/Fonts/Supplemental/Arial Bold.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"] if bold else
             ["/System/Library/Fonts/Supplemental/Arial.ttf",
              "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]) + \
            ["/System/Library/Fonts/Helvetica.ttc", "C:/Windows/Fonts/arial.ttf"]
    for c in cands:
        if os.path.exists(c):
            return ImageFont.truetype(c, size)
    return ImageFont.load_default(size=size)

def _draw_title(img, ex):
    from PIL import ImageDraw
    title, size = ex.label.rstrip("."), 54
    while size > 24 and _font(size, True).getlength(title) > W - 80:
        size -= 2
    ImageDraw.Draw(img).text((W // 2, 70), title, font=_font(size, True),
                             fill=(30, 30, 30), anchor="mm")

def _compose(ex, fonts):
    """Static frame for one example + cursor geometry for every written note."""
    from PIL import Image, ImageDraw
    bpl = 1                                      # one bar per line: big enough to read from the kit
    eng = Engraving(ex, page_width=1500 * bpl, bars_per_line=bpl)
    img = Image.new("RGB", (W, H), BG)
    _draw_title(img, ex)
    max_w, max_h = W - 120, H - 300
    zoom = min(max_w / eng.w, max_h / eng.h, 2.2)
    sc = eng.raster(zoom)
    x0 = (W - sc.shape[1]) // 2
    y0 = 160 + (max_h - sc.shape[0]) // 2
    frame = np.asarray(img).copy()
    frame[y0:y0 + sc.shape[0], x0:x0 + sc.shape[1]] = sc
    sp = eng.staff_space * zoom
    geo = {}
    for i, (x, y) in eng.pos.items():
        cx, cy = x0 + x * zoom, y0 + y * zoom          # notehead centre (3rd space)
        geo[i] = (cx, int(cy - 3.2 * sp), int(cy + 3.6 * sp))
    return frame, geo, max(5, int(0.55 * sp))

# ----- the book's own notation (when its PDF is available) -----
DPI = 250

def _book_staves(g):
    dark = g < 140; w = g.shape[1]
    lr = np.zeros(g.shape[0], int)
    for y in range(g.shape[0]):
        r = np.concatenate([[0], dark[y].astype(np.int8), [0]])
        d = np.diff(r); st = np.where(d == 1)[0]; en = np.where(d == -1)[0]
        lr[y] = (en - st).max() if len(st) else 0
    lines = np.where(lr > 0.55 * w)[0]
    groups = np.split(lines, np.where(np.diff(lines) > 2)[0] + 1)
    ys = [gg.mean() for gg in groups if len(gg)]
    out, i = [], 0
    while i < len(ys) - 4:
        d = np.diff(ys[i:i + 5])
        if np.all((d > 0.06 * DPI) & (d < 0.092 * DPI)):
            out.append((ys[i], ys[i + 4])); i += 5
        else:
            i += 1
    return out

def _book_heads(g, top, bot):
    """x-centres of full-size noteheads on a staff (graces, dots, clef are too narrow)."""
    from scipy import ndimage
    sp = (bot - top) / 4
    y0, y1 = int(top - sp), int(bot + sp)
    b = g[y0:y1] < 140
    for k in range(5):
        y = int(round(top + k * sp)) - y0
        b[y - 2:y + 3] = False
    b = ndimage.binary_closing(b, np.ones((5, 1)))
    o = ndimage.binary_opening(b, np.ones((7, 9)))
    lab, _ = ndimage.label(o)
    xs = []
    for sl in ndimage.find_objects(lab):
        wdt = sl[1].stop - sl[1].start; hgt = sl[0].stop - sl[0].start
        if wdt >= 0.072 * DPI and hgt >= 0.04 * DPI:
            k = max(1, int(round(wdt / (0.098 * DPI))))   # touching heads merge into one blob
            for j in range(k):
                xs.append(sl[1].start + (j + 0.5) * wdt / k)
    return sorted(xs)

def _norm(t):
    return re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()

def _book_headings(doc):
    """Every heading in the book ('16. Single Paradiddle Level 1', 'Level 2', ...) with its
    page, position (in DPI pixels), number (if printed) and levels. A printed line can hold
    two headings side by side; each ends with 'Level N' / 'Levels N and M'."""
    k = DPI / 72
    heads = []
    for pn in range(1, doc.page_count + 1):
        lines = {}
        for x0, y0, x1, y1, w, b, l, _ in doc[pn - 1].get_text("words"):
            lines.setdefault((b, l), []).append((x0 * k, y1 * k, w))
        for ws in lines.values():
            ws.sort()
            seg, i = [], 0
            while i < len(ws):
                seg.append(ws[i])
                if ws[i][2].lower() in ("level", "levels"):
                    j = i + 1
                    while j < len(ws) and re.fullmatch(r"\d|and|,", ws[j][2].lower()):
                        seg.append(ws[j]); j += 1
                    if j > i + 1:
                        text = " ".join(w for _, _, w in seg)
                        m = re.match(r"^(?:(\d+)\.\s*)?(.*?)\s*Levels?\b(.*)$", text)
                        heads.append(dict(page=pn, x=seg[0][0], y=max(y for _, y, _ in seg),
                                          number=int(m.group(1)) if m.group(1) else None,
                                          name=m.group(2).strip(),
                                          levels=frozenset(int(d) for d in re.findall(r"\d", m.group(3))),
                                          text=text))
                        seg = []
                    i = j
                    continue
                i += 1
    heads.sort(key=lambda h: (h["page"], h["y"], h["x"]))
    return heads

def book_layouts(pdf_path, examples, all_examples):
    """{id(example): layout} for the examples whose notation can be found in the book.
    Each example is matched to a heading by its number and level (in order, for repeats),
    then gets the lines of music below that heading, down to the next heading. Where two
    headings share a line, the notes are split at the second heading's position."""
    import fitz
    fitz.TOOLS.mupdf_display_errors(False)        # the book's fonts trigger harmless warnings
    doc = fitz.open(pdf_path)
    heads = _book_headings(doc)
    # numbers for headings that don't print one: the heading above's number, unless the
    # name clearly belongs to another rudiment in the file (e.g. 'Single Stroke Seven')
    import difflib
    names = {}
    for ex in all_examples:
        if ex.number is not None:
            names.setdefault(_norm(ex.name), ex.number)
    cur = None
    for h in heads:
        if h["number"] is None and h["name"]:
            best = max(names, key=lambda nm: difflib.SequenceMatcher(None, nm, _norm(h["name"])).ratio(),
                       default=None)
            if best and difflib.SequenceMatcher(None, best, _norm(h["name"])).ratio() >= 0.85:
                h["number"] = names[best]
        if h["number"] is None:
            h["number"] = cur
        cur = h["number"]
    # rows of headings, and the lines of music under each row
    cache = {}
    def page(pn):
        if pn not in cache:
            pix = doc[pn - 1].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY)
            g = np.frombuffer(pix.samples, np.uint8).reshape(pix.h, pix.w).copy()
            st = _book_staves(g)
            k = DPI / 72                      # blank the printed headings; the video has its own
            for x0, y0, x1, y1, *_ in doc[pn - 1].get_text("words"):
                g[max(0, int(y0 * k) - 3):int(y1 * k) + 3, max(0, int(x0 * k) - 3):int(x1 * k) + 3] = 255
            cache[pn] = (g, st)
        return cache[pn]
    # match the file's examples to headings: k-th example with a number+levels -> k-th heading
    match, used = {}, set()
    for ex in all_examples:
        if "page" in ex.book or ex.number is None:
            continue
        for i, h in enumerate(heads):
            if i not in used and h["number"] == ex.number and h["levels"] == frozenset(ex.levels):
                used.add(i); match[id(ex)] = i; h["ex"] = ex
                break
    rows = []
    for h in heads:
        if rows and rows[-1][0]["page"] == h["page"] and abs(rows[-1][0]["y"] - h["y"]) < 0.06 * DPI:
            rows[-1].append(h)
        else:
            rows.append([h])
    for r_i, row in enumerate(rows):
        pn = row[0]["page"]
        nxt = rows[r_i + 1][0]["y"] if r_i + 1 < len(rows) and rows[r_i + 1][0]["page"] == pn else 1e9
        _, st = page(pn)
        staff_ids = [i for i, (top, _) in enumerate(st) if row[0]["y"] < top < nxt]
        row.sort(key=lambda h: h["x"])
        # where two headings share a line, split the notes at the widest gap near the
        # second heading (the bar line / repeat sign between the two examples)
        cuts = []
        if staff_ids and len(row) > 1:
            g, _ = page(pn)
            top, bot = st[staff_ids[0]]
            hx = _book_heads(g, top, bot)
            before = 0                        # notes taken by the headings to the left
            for p_i, h in enumerate(row[1:]):
                gaps = [(hx[j + 1] - hx[j], (hx[j] + hx[j + 1]) / 2, j + 1) for j in range(len(hx) - 1)
                        if abs((hx[j] + hx[j + 1]) / 2 - h["x"]) < 0.8 * DPI]
                left = row[p_i].get("ex")
                need = (len([n for n in left.notes if not n["rest"]]) + len(left.book.get("skip", ()))
                        if left else None)
                fit = [gp for gp in gaps if need is not None and gp[2] - before == need]
                pick = max(fit or gaps) if gaps else (0, h["x"], before)
                cuts.append(pick[1]); before = pick[2]
        for p_i, h in enumerate(row):
            h["staffs"] = staff_ids
            h["lo"] = 0 if p_i == 0 else cuts[p_i - 1]
            h["hi"] = cuts[p_i] if p_i < len(cuts) else None
    heads_all = heads
    heads = [h for h in heads if h["staffs"]]
    out = {}
    for ex in examples:
        o = ex.book
        try:
            if "page" in o:                                   # explicit location in the file
                pn = o["page"]
                if not 1 <= pn <= doc.page_count:
                    raise RuntimeError(f"the PDF has no page {pn}")
                _, st = page(pn)
                same = sorted([c for c in heads if c["page"] == pn and set(o["lines"]) <= set(c["staffs"])],
                              key=lambda c: c["x"])
                h = (same[min(o.get("part", 0), len(same) - 1)] if same else
                     dict(page=pn, lo=0, hi=None, text=f"page {pn}"))
                h = dict(h, staffs=o["lines"])
            elif id(ex) in match and heads_all[match[id(ex)]]["staffs"]:
                h = heads_all[match[id(ex)]]
            else:
                continue
            out[id(ex)] = _book_layout(ex, h, *page(h["page"]), o.get("skip", set()))
        except RuntimeError as e:
            print(f"note: {ex.label} — can't use the PDF ({e}); drawing the notation instead",
                  file=sys.stderr)
    return out

def _book_layout(ex, h, g, st, skip):
    written = [n for n in ex.notes if not n["rest"]]
    rows, seen = [], 0
    for s_i in h["staffs"]:
        if s_i >= len(st):
            raise RuntimeError(f"page {h['page']} has only {len(st)} lines of music")
        top, bot = st[s_i]
        hx = [x for x in _book_heads(g, top, bot)
              if x >= h["lo"] and (h["hi"] is None or x < h["hi"])]
        keep = [x for i, x in enumerate(hx) if seen + i not in skip]   # 'skip' counts from 1st note
        seen += len(hx)
        rows.append(dict(top=top, bot=bot, heads=keep))
    found = sum(len(r["heads"]) for r in rows)
    if found != len(written):
        raise RuntimeError(f"expected {len(written)} notes under '{h['text']}' on page "
                           f"{h['page']}, found {found}")
    xs_all = []
    for r_i, r in enumerate(rows):
        xs_all += [(r_i, x) for x in r["heads"]]
        r["x0"] = int(h["lo"])
        r["x1"] = g.shape[1] if h["hi"] is None else int(h["hi"])
    return dict(g=g, rows=rows, notes={w["i"]: xy for w, xy in zip(written, xs_all)})

def _compose_book(ex, lay, fonts):
    """Like _compose, but showing the example's lines cropped from the book's pages."""
    from PIL import Image, ImageDraw
    from scipy import ndimage
    img = Image.new("RGB", (W, H), BG)
    _draw_title(img, ex)
    g = lay["g"]
    crops, geo0 = [], []
    for r in lay["rows"]:
        sp = (r["bot"] - r["top"]) / 4
        y0 = max(0, int(r["top"] - 5.2 * sp)); y1 = min(g.shape[0], int(r["bot"] + 3.6 * sp))
        c = g[y0:y1, r["x0"]:r["x1"]].copy()
        lab, _ = ndimage.label(c < 200)            # drop bits of neighbouring lines cut by the crop
        for edge in (lab[0], lab[-1]):
            for v in set(edge[edge > 0].tolist()):
                c[lab == v] = 255
        crops.append(c); geo0.append((y0, r["x0"]))
    max_w, max_h = W - 120, H - 300
    total_h = sum(c.shape[0] for c in crops) + 30 * (len(crops) - 1)
    scale = min(max_w / max(c.shape[1] for c in crops), max_h / total_h, 1.6)
    y = 160 + (max_h - total_h * scale) / 2
    rects = []
    for c, (cy0, cx0), r in zip(crops, geo0, lay["rows"]):
        im = Image.fromarray(c).resize((int(c.shape[1] * scale), int(c.shape[0] * scale)),
                                       Image.LANCZOS).convert("RGB")
        x = (W - im.width) // 2
        img.paste(im, (x, int(y)))
        sp = (r["bot"] - r["top"]) / 4
        rects.append((x - cx0 * scale, int(y + (r["top"] - 1.6 * sp - cy0) * scale),
                      int(y + (r["bot"] + 1.6 * sp - cy0) * scale)))
        y += im.height + 30
    geo = {}
    for i, (r_i, x) in lay["notes"].items():
        ox, ya, yb = rects[r_i]
        geo[i] = (ox + x * scale, ya, yb)
    return np.asarray(img).copy(), geo, max(6, int(scale * 0.06 * DPI))

def render_video(timeline, pcm, out_mp4, book=None):
    from PIL import Image, ImageDraw
    os.makedirs(os.path.dirname(os.path.abspath(out_mp4)), exist_ok=True)
    fonts = dict(title=_font(54, True), info=_font(40), count=_font(90, True))
    def patch(text, font, color):
        im = Image.new("RGB", (900, 120), BG); ImageDraw.Draw(im).text(
            (450, 60), text, font=font, fill=color, anchor="mm")
        return np.asarray(im)
    status_cache = {}
    def status(key):
        if key not in status_cache:
            if key[0] == "count":
                im = Image.new("RGB", (900, 120), BG); dr = ImageDraw.Draw(im)
                for i in range(4):
                    dr.text((225 + i * 150, 60), str(i + 1), font=fonts["count"],
                            fill=(200, 70, 30) if i < key[1] else (205, 200, 190), anchor="mm")
                status_cache[key] = np.asarray(im)
            elif key[0] == "pass":
                status_cache[key] = patch(f"Pass {key[1]} of {key[2]}  ·  {key[3]:g} bpm",
                                          fonts["info"], (110, 110, 110))
            else:
                status_cache[key] = patch("", fonts["info"], (0, 0, 0))
        return status_cache[key]
    SX, SY = (W - 900) // 2, H - 150

    wav_tmp = out_mp4 + ".audio.wav"
    wavfile.write(wav_tmp, SR, pcm)
    nframes = int(len(pcm) / SR * FPS) + 1
    ff = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-i", wav_tmp,
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", out_mp4],
        stdin=subprocess.PIPE)
    try:
        f = 0
        for n_take, item in enumerate(timeline):
            parts = item["parts"]
            starts = [p["start"] for p in parts]
            bases = {}
            while f < nframes and f / FPS < item["end"]:
                t = f / FPS
                k = max(0, int(np.searchsorted(starts, t, side="right")) - 1)
                part = parts[k]
                if k not in bases:
                    lay = (book or {}).get(id(part["ex"]))
                    bases[k] = (_compose_book(part["ex"], lay, fonts) if lay
                                else _compose(part["ex"], fonts))
                frame, geo, half = bases[k]
                saved = None
                if t >= item["play"]:
                    played, L = part["played"], part["length"]
                    onsets = part.setdefault("_on", np.array([float(pt) for _, pt in played]))
                    beat = part["beat"]
                    p = min(int((t - part["start"]) / (L * beat)), item["passes"] - 1)
                    tb = min((t - part["start"]) / beat - p * L, L)
                    i = max(int(np.searchsorted(onsets, tb, side="right") - 1), 0)
                    cx, ya, yb = geo[played[i][0]["i"]]
                    if i + 1 < len(played):              # glide toward the next note on the line
                        nx, na, _ = geo[played[i + 1][0]["i"]]
                        if na == ya and nx > cx:
                            fr = (tb - onsets[i]) / (onsets[i + 1] - onsets[i])
                            cx += (nx - cx) * min(max(fr, 0), 1)
                    cx = int(cx)
                    xa, xb = max(cx - half, 0), min(cx + half, W)
                    ya, yb = max(ya, 0), min(yb, H)
                    saved = (ya, yb, xa, xb, frame[ya:yb, xa:xb].copy())
                    frame[ya:yb, xa:xb] = (frame[ya:yb, xa:xb] * 0.55 + CURSOR * 0.45).astype(np.uint8)
                    st = status(("pass", p + 1, item["passes"], part["bpm"]))
                elif t >= item["count"]:
                    st = status(("count", min(4, int((t - item["count"]) / item["count_beat"]) + 1)))
                else:
                    st = status(("blank",))
                frame[SY:SY + 120, SX:SX + 900] = st
                ff.stdin.write(frame.tobytes())
                if saved:
                    ya, yb, xa, xb, blk = saved
                    frame[ya:yb, xa:xb] = blk
                f += 1
            print(f"video {n_take + 1}/{len(timeline)}", file=sys.stderr, end="\r")
        ff.stdin.close(); ff.wait()
    finally:
        if os.path.exists(wav_tmp):
            os.remove(wav_tmp)
    print(f"\nwrote {out_mp4}", file=sys.stderr)

# ======================= command line =======================
def cli():
    global TAP, GRACE, BPM_OVERRIDE, VOICE
    import argparse
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(
        description="Make drum rudiment practice audio, a PDF sheet and a notation video "
                    "from the patterns in a rudiments file.",
        epilog="Examples:\n"
               "  python3 rudiment_generator.py practice.mp3\n"
               "  python3 rudiment_generator.py --sheet rudiments.pdf --levels 1,2,3\n"
               "  python3 rudiment_generator.py practice.mp3 --video practice.mp4 --levels 1,2,3 --seamless\n"
               "  python3 rudiment_generator.py --rudiments mine.txt --only 41 --video mine.mp4\n"
               "  python3 rudiment_generator.py practice.mp3 --video practice.mp4 --pdf 40rudiments.pdf\n"
               "  python3 rudiment_generator.py nonstop.mp3 --continuous --passes 1",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("output", nargs="?",
                   help="audio file to write; .mp3 is encoded with ffmpeg, anything else is WAV "
                        "(default: rudiments.mp3 if no --sheet or --video is given)")
    p.add_argument("--rudiments", default=os.path.join(here, "rudiments.txt"), metavar="FILE",
                   help="pattern file (default: rudiments.txt next to this script)")
    p.add_argument("--sheet", metavar="OUT.pdf", help="write a PDF practice sheet of the notation")
    p.add_argument("--video", metavar="OUT.mp4",
                   help="write a video of the notation with a cursor following the audio")
    p.add_argument("--pdf", metavar="FILE",
                   help="the '40 Rudiments Plain and Simple' PDF: the video then shows the book's "
                        "own notation for every example it can find there by number and level "
                        "(default: 40rudiments.pdf here or next to this script, if it exists)")
    p.add_argument("--no-pdf", action="store_true",
                   help="always draw the notation, even if the book PDF is found")
    p.add_argument("--levels", default="1,2", metavar="LIST",
                   help="comma-separated levels to include, e.g. 1 or 1,2 or 1,2,3 or 2,3 "
                        "(default: %(default)s)")
    p.add_argument("--only", metavar="LIST",
                   help="only these rudiments: comma-separated numbers and/or parts of names, "
                        "e.g. 16,19 or paradiddle")
    p.add_argument("--seamless", action="store_true",
                   help="play a rudiment's levels back to back with no pause: one spoken name "
                        "and count-in, then each level in turn (each played --passes times)")
    p.add_argument("--continuous", action="store_true",
                   help="no pauses: one count-in, then every rudiment straight after the "
                        "previous one, with its name spoken over its first bars")
    p.add_argument("--bpm", type=float, default=None,
                   help="tempo for everything, overriding any bpm in the rudiments file "
                        f"(default: the file's bpm, else {DEFAULT_BPM})")
    p.add_argument("--passes", type=int, default=2,
                   help="times each example is played (default: %(default)s)")
    p.add_argument("--tap", type=float, default=TAP, metavar="LEVEL",
                   help="volume of unaccented strokes, 0-1, relative to accents at 1.0. "
                        "Lower = louder-sounding accents (default: %(default)s)")
    p.add_argument("--grace", type=float, default=None, metavar="LEVEL",
                   help="volume of flam/drag grace notes, 0-1 (default: about half of --tap)")
    p.add_argument("--no-click", action="store_true",
                   help="no metronome click under the examples (count-in still plays)")
    p.add_argument("--voice", metavar="NAME",
                   help="macOS voice for the spoken names, e.g. Samantha or 'Ava (Premium)'. "
                        "List installed voices with: say -v '?'  (default: system voice)")
    a = p.parse_args()
    try:
        levels = {int(x) for x in a.levels.split(",") if x.strip()}
    except ValueError:
        p.error("--levels takes a comma-separated list, e.g. 1,2 or 2,3")
    if not levels or not levels <= {1, 2, 3}:
        p.error("--levels can only use 1, 2 and 3")
    if not 0 < a.tap <= 1:
        p.error("--tap must be between 0 and 1")
    if not (a.output or a.sheet or a.video):
        a.output = "rudiments.mp3"
    TAP, VOICE = a.tap, a.voice
    GRACE = a.grace if a.grace is not None else round(a.tap * 0.52, 3)
    if a.bpm is not None and not 10 <= a.bpm <= 400:
        p.error("--bpm must be between 10 and 400")
    BPM_OVERRIDE = a.bpm

    examples = load_rudiments(a.rudiments)
    only = [x.strip() for x in a.only.split(",") if x.strip()] if a.only else None
    chosen = select(examples, levels, only)
    if not chosen:
        sys.exit("No examples match those --levels / --only choices.")
    print(f"{len(chosen)} examples from {a.rudiments}", file=sys.stderr)

    if a.sheet:
        lv = sorted(levels)
        write_sheet(chosen, a.sheet, "Drum Rudiments — Level" + ("s " if len(lv) > 1 else " ")
                    + ", ".join(map(str, lv)))
    if a.output or a.video:
        tmpdir = os.path.dirname(os.path.abspath(a.output or a.video))
        os.makedirs(tmpdir, exist_ok=True)
        render = render_audio_continuous if a.continuous else render_audio
        pcm, timeline = render(make_chains(chosen, a.seamless), levels, a.passes,
                               not a.no_click, tmpdir)
        if a.output:
            write_audio(pcm, a.output)
        if a.video:
            book = None
            pdf = None if a.no_pdf else a.pdf
            if pdf is None and not a.no_pdf:
                for c in ("40rudiments.pdf", os.path.join(here, "40rudiments.pdf")):
                    if os.path.exists(c):
                        pdf = c
                        break
            if pdf and not os.path.exists(pdf):
                print(f"note: {pdf} not found; drawing the notation instead", file=sys.stderr)
                pdf = None
            if pdf:
                print(f"using the book's notation from {pdf}", file=sys.stderr)
                book = book_layouts(pdf, chosen, examples)
            render_video(timeline, pcm, a.video, book)

if __name__ == "__main__":
    cli()
