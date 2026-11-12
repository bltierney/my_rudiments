# my_rudiments

Python script to generate custom drum rudiment play-along audio, videos and practice sheets.

`my_rudiments.py` reads drum patterns from a plain text file (`rudiments.txt`) and makes:

- **Audio (MP3 or WAV).** For each rudiment, a voice says its name, a click counts you in, and then the rudiment plays. Accents, flams, drags, diddles and buzzes all sound distinct. Right-hand strokes are panned slightly right and left-hand strokes slightly left.
- **A PDF practice sheet** with the notation and sticking.
- **A video** of the notation with a cursor that follows the audio note by note.

The included `rudiments.txt` comes with 76 rudiment examples at Levels 1, 2 and 3. You can edit those or add your own.

---

## Setup

You need Python 3 and a few packages:

```bash
pip install --prefer-binary numpy scipy verovio pymupdf pillow
```

`--prefer-binary` makes pip use a ready-made package instead of trying to compile one; see [Troubleshooting](#troubleshooting) if verovio fails to install.

You also need two programs:

- **ffmpeg**, to make MP3 and MP4 files.
  - macOS: `brew install ffmpeg`
  - Linux: `sudo apt install ffmpeg`
- **A text-to-speech voice**, for the spoken names.
  - macOS: nothing to install; the built-in `say` command is used.
  - Linux: `sudo apt install espeak-ng`

Run the program from the folder that holds your `rudiments.txt`; that's where it looks by default. `my_rudiments.py` itself can live anywhere, e.g. `python3 ~/Claude/my_rudiments/my_rudiments.py practice.mp3`. To use a file somewhere else, pass `--rudiments FILE`.


---

## Quick start

```bash
python3 my_rudiments.py practice.mp3
```

This makes `practice.mp3` with every Level 1 and 2 rudiment at 70 bpm, each played twice.

To see every option:

```bash
python3 my_rudiments.py --help
```

---

## Examples

### Audio

```bash
# Levels 1 and 2 (the default)
python3 my_rudiments.py practice.mp3

# Level 3 only, at 90 bpm
python3 my_rudiments.py level3.mp3 --levels 3 --bpm 90

# All three levels; each rudiment plays Level 1, then 2, then 3 with no pause
python3 my_rudiments.py all-levels.mp3 --levels 1,2,3 --seamless

# Just the paradiddles (any rudiment whose name contains "paradiddle")
python3 my_rudiments.py paradiddles.mp3 --only paradiddle

# Rudiments 16, 17 and 18, each played 4 times, without the metronome click
python3 my_rudiments.py diddles.mp3 --only 16,17,18 --passes 4 --no-click

# Exaggerated accents: unaccented strokes very quiet
python3 my_rudiments.py accents.mp3 --tap 0.08

# Play straight through with no pauses and no voice, top to bottom
python3 my_rudiments.py nonstop.mp3 --continuous --passes 1
```

### PDF sheet

```bash
# All Level 1, 2 and 3 notation as a printable PDF
python3 my_rudiments.py --sheet rudiments.pdf --levels 1,2,3

# A sheet of just the flam rudiments (every name containing "flam")
python3 my_rudiments.py --sheet flams.pdf --only flam --levels 1,2,3
```

When you ask only for `--sheet` (or only for `--video`), no audio file is written.

### Video

```bash
# Video plus a matching MP3
python3 my_rudiments.py practice.mp3 --video practice.mp4

# Video only, all levels, seamless, at 80 bpm with each example played 4 times
python3 my_rudiments.py --video practice-80bpm.mp4 --levels 1,2,3 --seamless --bpm 80 --passes 4

# Video without the "Next up" panel, so the current score is bigger
python3 my_rudiments.py --video practice.mp4 --no-next-up

# Show the notation from a PDF you have, instead of drawn notation
python3 my_rudiments.py --video practice.mp4 --pdf ~/Documents/my-rudiment-notation.pdf

# Everything at once
python3 my_rudiments.py practice.mp3 --sheet practice.pdf --video practice.mp4 --levels 1,2,3
```

Each example in the video has:

- its name across the top,
- a "1 2 3 4" count-in indicator,
- a "Pass 1 of 2 · 70 bpm" status line,
- an orange cursor that moves from note to note in time with the audio,
- a "Next up" panel at the bottom showing the next example's title and first line of music (the next level, during a `--seamless` take). `--no-next-up` leaves it out and gives the current score more room.

A full video of all levels takes a few minutes to render.

### Voice

```bash
# Use a specific macOS voice
python3 my_rudiments.py practice.mp3 --voice Samantha

# Use a downloaded "Premium" voice (quote names with spaces)
python3 my_rudiments.py practice.mp3 --voice "Ava (Premium)"
```

To list your Mac's voices, run `say -v '?'`.

The Enhanced and Premium voices sound much more natural. To download one, open System Settings, go to Accessibility → Spoken Content, and click the "i" next to System Voice.

---

## Options

| Option | What it does | Default |
|---|---|---|
| `output` | Audio file to write. A name ending in `.mp3` gives MP3; anything else gives WAV. | `rudiments.mp3` (only if no `--sheet`/`--video`) |
| `--rudiments FILE` | Pattern file to read | `rudiments.txt` in the current folder |
| `--sheet OUT.pdf` | Write a PDF practice sheet | |
| `--video OUT.mp4` | Write a notation video with a moving cursor | |
| `--pdf FILE` | A PDF of rudiment notation to show in the video instead of drawn notation | drawn notation |
| `--levels LIST` | Levels to include, e.g. `1`, `1,2`, `2,3`, `1,2,3` | `1,2` |
| `--only LIST` | Only these rudiments: numbers and/or parts of names, e.g. `16,19` or `paradiddle` | all |
| `--seamless` | Play a rudiment's levels back to back with no pause: one spoken name and count-in, then each level | off |
| `--continuous` | No pauses and no voice: one count-in, then every rudiment straight after the previous one | off |
| `--bpm BPM` | Tempo for everything; overrides any `bpm:` in the file | file's `bpm:`, else 70 |
| `--passes N` | Times each example is played | 2 |
| `--tap LEVEL` | Volume of unaccented strokes (0–1, accents are 1.0). Lower means louder-sounding accents | 0.15 |
| `--grace LEVEL` | Volume of flam and drag grace notes (0–1) | about half of `--tap` |
| `--no-next-up` | In the video, leave out the "Next up" preview of the following example | shown |
| `--no-click` | No metronome click under the examples (the count-in still plays) | |
| `--voice NAME` | macOS voice for the spoken names | system voice |

Some notes on how the options work:

- **Repeat signs:** the sample `rudiments.txt` has none; use `--passes` to set how many times each example plays. If you add repeat signs (`|: :|`) to a pattern, that part plays twice within each pass.
- **`--seamless`:** levels of the same rudiment are joined only when they appear in order (Level 1, then 2, then 3) under the same number.
- **`--continuous`:** the video still shows each rudiment's name, and the score switches the moment the next rudiment starts.

---

## The rudiments file

`rudiments.txt` is plain text that you can edit in any text editor. Each example starts with its name in square brackets, followed by `key: value` lines:

```
[Single paradiddle]
number: 16
level: 1
pattern: @4 R> L R R L> R L L R> L R R L> R L L
```

| Key | Meaning |
|---|---|
| `[Name]` | The rudiment's name, as spoken and printed. |
| `number:` | Optional. The rudiment's number; used for the spoken name, `--only`, `--seamless`, and finding it in a `--pdf` file. |
| `level:` | `1`, `2` or `3`, or a list like `2,3`. Default `1`. |
| `time:` | Optional. Beats per bar over 4, e.g. `3/4`. Default `4/4`. |
| `bpm:` | Optional. Tempo for this example only. |
| `pdf:` | Optional and rarely needed; see [Using notation from a PDF](#using-notation-from-a-pdf). |
| `pattern:` | The notes. Long patterns can continue on the following lines if those lines are indented. |

Lines starting with `#` are comments.

### Pattern codes

Separate every code with a space.

| Code | Meaning | Example |
|---|---|---|
| `R` `L` | A stroke with the right / left hand | `R L R R` |
| `>` | Accent (written after the stroke) | `R>` |
| `f` | Flam: a grace note from the other hand | `fR>` |
| `g` | Drag: two grace notes from the other hand | `gL` |
| `~` | Diddle: two strokes with the same hand | `R~` |
| `z` | Buzz stroke | `Rz` |
| `.` | Rest | `R L . L` |
| `@N` | From here on, N notes per beat | see below |
| `\|` | Bar line (optional; it's checked, but bars fill automatically) | |
| `\|:` `:\|` | Repeat signs; the part between them is played twice | `\|: ... :\|` |

The codes can be combined in this order: grace, hand, diddle or buzz, accent. For example, `fR~>` is a flammed, accented diddle.

Common `@N` values:

| `@N` | Note value |
|---|---|
| `@1` | quarter notes |
| `@2` | eighth notes |
| `@3` | eighth-note triplets |
| `@4` | sixteenth notes (the default) |
| `@6` | sextuplets |
| `@8` | thirty-second notes |

You can switch partway through a bar. For example, `@6 R L R @2 L` is three sextuplet notes followed by an eighth note.

Each pattern must add up to whole bars. If it doesn't, the script says which example and line is wrong and how many beats it found.

### Tempo

The `bpm: 70` line near the top of `rudiments.txt` sets the tempo for the whole file. You can override it in three ways:

- **For one example:** add a `bpm:` line to that entry. Each level is a separate entry, so different levels can have different tempos; with `--seamless`, the tempo changes at the level boundary.
- **For everything, one time:** use `--bpm` on the command line. It overrides every tempo in the file.
- **No tempo set anywhere:** the tempo is 70.

### Pronunciation

If the voice mispronounces a word, add a respelling near the top of the file, before the first `[Name]`:

```
pronounce: ratamacue = rat-uh-muh-cue
pronounce: pataflafla = pat a fla fla
```

Only the spoken name changes; the video and sheet keep the real spelling.

To try a spelling first, run `say "rat-uh-muh-cue"`.

### Adding your own rudiments

Add entries to `rudiments.txt`, or put them in your own file and pass it with `--rudiments`. For example, a file `mine.txt`:

```
bpm: 80

[Paradiddle-diddle-diddle]
number: 41
level: 1
pattern: @6 R> L R R L L R R L L R L L> R L L R R L L R R L R

[Paradiddle-diddle-diddle]
number: 41
level: 2
bpm: 100
pattern: @6 R> L R R L L R R L L R L L> R L L R R L L R R L R

[Flam taps and buzzes]
level: 1
pattern: @4 fR> R fL> L fR> R fL> L fR> R fL> L fR> R fL> L |
         @2 Rz Lz Rz Lz Rz Lz Rz Lz

[Waltz singles]
time: 3/4
pattern: @2 R> L R L R L
```

```bash
# Audio, sheet and video of your own file
python3 my_rudiments.py --rudiments mine.txt mine.mp3 --sheet mine.pdf --video mine.mp4

# Just rudiment 41, Levels 1 and 2 back to back
python3 my_rudiments.py --rudiments mine.txt --only 41 --levels 1,2 --seamless pdd.mp3
```

Entries without a `number:` are spoken by name only ("Waltz singles. Level 1.").

---

## Using notation from a PDF

By default, the video shows notation drawn by the program. If you have a PDF of rudiment notation, you can point to it with `--pdf` and the video will show that PDF's notation instead, cropped from its pages, with the cursor following the notes.

```bash
python3 my_rudiments.py --video practice.mp4 --pdf my-notation.pdf
```

This works with a PDF laid out like a typical rudiment sheet:

- **Headings are real text**, not a scanned image, with the number, name and level, like `16. Single Paradiddle Level 1`. Headings that say only `Level 2` (or, say, `Off the left hand Level 2`) belong to the rudiment above them.
- **Each heading sits above its music**, on five-line staves with one note per stroke. An example can run over several lines, down to the next heading.
- **Two examples can share a line**, side by side, each with its own heading above it.

Each example in `rudiments.txt` is matched to a heading by its `number:` and `level:`, and its notes are matched to the noteheads on the page.

- **No match:** an example that isn't found in the PDF, or whose note count doesn't match the page, gets drawn notation instead, in the same video. The script prints a note saying which ones.
- **The sheet:** `--sheet` always uses drawn notation.

You rarely need a `pdf:` line in an entry. Use one only to point an example somewhere specific:

```
pdf: page 9, line 5, part 2    # the right-hand example on the 5th line of page 9
pdf: page 10, lines 4-5        # an example that spans two lines
pdf: skip 13,30                # ignore noteheads 13 and 30 (e.g. tied notes that aren't struck)
```

---

## Troubleshooting

- **`unrecognized arguments`:** your copy of the script is older than the option you're using. Get the latest `my_rudiments.py`, and check `--help` for the option.
- **`ModuleNotFoundError: No module named ...`:** install the missing package with `pip install --prefer-binary numpy scipy verovio pymupdf pillow`. If pip says the environment is "externally managed," use a virtual environment:
  ```bash
  python3 -m venv ~/rudenv && source ~/rudenv/bin/activate
  pip install --prefer-binary numpy scipy verovio pymupdf pillow
  ```
- **`Failed building wheel for verovio`:** pip tried to compile verovio because the newest version has no ready-made package for your Mac (newest verovio needs macOS 13 Ventura or later). Install with `--prefer-binary` so pip picks the newest version that does have one:
  ```bash
  pip install --prefer-binary verovio
  ```
  If that still fails, update pip first (`python3 -m pip install --upgrade pip`) and try again.
- **`ffmpeg not found`:** install ffmpeg (see [Setup](#setup)). Until you do, MP3 output is left as a WAV file, and the script tells you where it is.
- **`No text-to-speech found`:** on Linux, install `espeak-ng`.
- **An error naming a line in `rudiments.txt`:** the message gives the file, line, rudiment and the problem. For example, `can't read 'X' in the pattern` or `pattern is 5 beats long, which isn't a whole number of 4-beat bars`.
- **A note says the PDF couldn't be used for an example:** the script couldn't match that example's notes to the PDF. It draws the notation for that example instead, and everything else is unaffected.
