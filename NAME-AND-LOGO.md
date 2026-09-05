# scoot: name and logo

Working notes for taking `corpcli` public as a multi-backend coding agent (Claude, OpenAI, Copilot, Ollama).
Decision so far: name it **scoot**, PyPI package `scootcli`, command `scoot`.

## What the name has to say

The project is stdlib only, ships as a single-file zipapp, and extends through drop-in registries.
It is not "small for its own sake".
It is "sharpened to fit one programmer", the opposite of limitless.
The name should be short, typeable all day, kid-friendly, and free of collisions with tools in the same category.

## Naming rules learned along the way

- Every plain English word is taken on PyPI, but the package name and the command name do not have to match.
  `pip install scootcli` followed by typing `scoot` is normal.
- The command must not already live on PATH.
  That rules out `kit`, `plain`, `lean`, `just`, `nano`, `micro`, `pico`.
- Avoid being one letter away from a popular tool in the same category.

## Candidates considered

### Rejected

| name | why not |
|---|---|
| mycli | the well-known MySQL client |
| minicli | taken on PyPI |
| opencode | taken |
| scooter | taken on PyPI, and `thomasschafer/scooter` (1.3k stars) is a terminal find-and-replace tool for programmers: the closest same-category neighbour. Different word, different job, so `scoot` still stands; `scootcli` is the free variant |
| good cli | too generic to search for or remember |
| mod / mods | `charmbracelet/mods` (4.5k stars, "AI on the command line") is one letter away; `go mod` is a daily command; `mod` exists on PyPI |
| hone | best meaning ("a coding agent you sharpen yourself"), but kids do not know the word |

### Shortlist, ranked before the kid-friendly constraint

1. **hone** (`honecli`): custom-fit and small in one syllable, zero collisions.
2. **mote** (`motecli`): the smallest visible particle, quiet and clean.
3. **smol** (`smolcli` / `smolcode`): internet-native, instantly readable, slight joke-name risk.
4. **bare** (`barecli`): stdlib only, but tilts toward "stripped down" rather than "tailored".
5. **enough** (`enoughcli`): the literal opposite of limitless, awkward to type all day.
6. **scoot** (`scootcli`): playful, memorable, free.

### Kid-friendly backups

| name | PyPI | GitHub | note |
|---|---|---|---|
| scootcli, command `scoot` | free | none | chosen |
| tweakcli, command `tweak` | free | none | "you tweak it"; `tweak` itself exists on PyPI |
| tinkercli, command `tinker` | free | nothing relevant | closest in meaning to hone, three syllables |

### Availability table (checked 2026-09-05)

| name | PyPI package | GitHub top hit |
|---|---|---|
| scootcli | free | none |
| honecli / honecode | free | none |
| motecli / motecode | free | nothing relevant |
| smolcli / smolcode | free | smolcoder, 24 stars |
| barecli / barecode | free | unrelated Java repo |
| enoughcli | free | none |
| tweakcli | free | none |
| tinkercli | free | nothing relevant |
| modcli / modcode | free | minor Minecraft repos, plus the `mods` problem above |

Other free compounds if ever needed: `lilcli`, `plaincli`, `tinycode`, `lilcode`, `pocketcode`, `owncode`, `stdcode`, `stdagent`, `kodo`, `picocli`.

## Why scoot

Kids know it, it is a verb ("scoot over", "let's scoot"), it sounds fast and small, and it has zero collisions.
A scooter is the right metaphor: personal transport you tune yourself, not a limitless bus.

Tagline candidate: **scoot: a tiny coding agent that goes where you point it.**

Known neighbours (checked 2026-09-05): `scoot` on PyPI is a dead Twitter Scoot daemon client (last release 2016), and `brew install scoot` is a macOS cursor-actuator cask (`mjrusso/scoot`, 532 stars).
Neither puts a `scoot` binary on PATH, so the command is safe, but a Homebrew formula would have to be `scootcli`.
Because a `scoot` module exists on PyPI, the Python import package is `scootcli` too: distribution name, import name, and site-packages directory all agree, and the command stays `scoot`.

Todo: grab `scootcli` on PyPI and the GitHub repo name early, short names disappear fast.
PyPI does not need a public repo: the first upload claims the name, and the repository URL in the metadata is optional.

## Logo

One character everywhere: square-head robot, one arm on the handlebar, standing on the deck, two wheels.
Drafts A and B are the same drawing, so the mascot stays recognizable across README and terminal.

### A. Plain ASCII, README hero

Works in every font and on GitHub.

```
       .---.
       |o_o|
    T__|---|
    |  |_|_|
  (o)=======(o)
```

### B. Box-drawing, REPL banner

Same pose, cleaner lines on unicode terminals.

```
       ╭───╮
       │o o│
    T──┤───┤
    │  ╰┬─┬╯
  (o)═══╧═╧═(o)
```

### C. Leaning forward with speed lines

More personality, reads as "going somewhere".

```
          ╭───╮
        ─ │> >│
       ─T─┤───┤
        │ ╰┬─┬╯
      (o)══╧═╧══(o)
```

### D. Three-line micro version

For `/help` headers or the top of the status panel.

```
       ╭o_o╮
     T─┤   │
     │ ╰┬─┬╯
 (o)═╧══════(o)
```

### E. Prompt labels

The kick scooter emoji (U+1F6F4) exists, so the turn labels can become:

```
  ❯ you
  🛴 scoot
```

Fall back to the plain `⏺` label when the terminal has no emoji font.
Terminals do not expose whether an emoji font is present, so this is a setting (`--no-emoji`, `SCOOT_EMOJI=0`) rather than detection.
`🛴` is a double-width glyph, so the label text starts one column later than after `❯`; the labels sit on their own lines, so nothing needs re-aligning.

### Recommendation

- B as the default banner.
- A in the README.
- D for compact spots.
- 🛴 label with a `⏺` fallback.

### Implementation notes

- Keep the art in one module with a `--no-logo` switch.
  The banner burns six rows on every launch and some people will hate that.
- Let the eyes change with state: `o o` idle, `> >` while thinking, `- -` when it stops.
  Cheap to do, and it is the kind of small delight the OpenClaw crab gives.
- Shipped (2026-09-05) as `scootcli/logo.py`: the banner lines sit beside draft B instead of under a box, so the mascot costs no extra rows.
  The face (`╭o o╮`) leads the status bar and its eyes follow the turn; draft D heads `/help`; `/logo on|off` persists the choice, `--no-logo` and `SCOOT_LOGO=0` override per launch.
  The state-driven eyes live in the status bar, not the banner, because the banner is printed once and the bar is the only place the mascot is on screen while the agent works.

### Open item: wordmark

A hand-drawn "scoot" figlet next to the robot was not clean enough to ship.
If a wordmark banner is wanted, install figlet, pick a font, and fit the robot to it.
