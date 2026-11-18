<pre>
 ██████╗ █████╗ ██████╗ ██████╗  ██████╗ ████████╗
██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝
██║     ███████║██████╔╝██████╔╝██║   ██║   ██║
██║     ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║
╚██████╗██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝
</pre>

**Download and convert video in your terminal.** Paste a link from YouTube or any of
1,300+ other sites, press `d`, and it's saved. Carrot also converts files already on your
computer to 51 formats.

[![PyPI](https://img.shields.io/pypi/v/carrot-leaf)](https://pypi.org/project/carrot-leaf/)
![Platforms](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

[Install](#install) · [Quick start](#quick-start) · [Keys](#keys) · [Features](#features) ·
[Command line](#command-line) · [Formats](#formats) · [Settings and files](#settings-and-files) ·
[Troubleshooting](#troubleshooting) · [Development](#development)

---

## Install

**macOS and Linux**

```sh
curl -LsSf https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.sh | sh
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.ps1 | iex"
```

The script installs everything Carrot needs: [uv](https://docs.astral.sh/uv/), the app,
its own yt-dlp and Deno, and ffmpeg (about 50 MB). You don't need anything set up first.

<details>
<summary>Already use uv or pipx?</summary>

```sh
uv tool install carrot-leaf
pipx install carrot-leaf      # needs Python 3.10+
carrot setup                  # optional: get ffmpeg now instead of on first launch
```

To skip the ffmpeg download in the install script, set `CARROT_LEAF_NO_FFMPEG=1`.
Carrot then fetches it in the background the first time you open it.

</details>

## Quick start

1. Run `carrot`.
2. Paste a link. The title, length, upload date and thumbnail appear.
3. Press `tab`, then `d` for the best MP4 or `c` to choose a format.

> [!TIP]
> While the URL bar has focus, letters type into the link. Press `tab` to use the keys,
> `esc` to go back. You can also click any key in the bottom bar.

## Keys

| Key | Does |
| --- | --- |
| **Get videos** | |
| `d` | Download at the best quality, as MP4 |
| `c` | Convert: pick a format and quality, a clip, subtitles and more |
| `e` | Pick which videos of a playlist or channel to get |
| `v` | Load the link Carrot found on your clipboard |
| `x` | Cancel the current download |
| `p` | Pause the queue. Running downloads finish, waiting ones hold |
| **Your library** | |
| `h` | History of everything you've saved |
| `a` | Follow this playlist or channel, or this video's channel |
| `w` | What's new in everything you follow |
| `/` | Search what was said in your downloads |
| `o` | Show the last download in Finder or Explorer |
| **App** | |
| `ctrl+t` | Switch between Download and Convert files |
| `s` | Settings: download folder, file names, cookies |
| `t` | Pick a theme |
| `l` | List supported sites, or paste a link to check it |
| `u` | Update yt-dlp |
| `q` | Quit |

## Features

### Queue and clipboard

- Paste the next link while one downloads. Downloads run one at a time, or up to four at
  once if you set that in Settings.
- When Carrot is in the background and the queue finishes, you get a system notification.
- Copy a link anywhere and switch back to Carrot. It offers the link, and `v` loads it.
  You can turn this off in Settings.

### Playlists and channels

- The card shows how many videos the link has. `d` gets them all, `c` sets one format for
  all of them.
- `e` opens a list to tick the ones you want. `a` ticks all, `n` none, and a number keeps
  the first N (the newest, on a channel). Up to 500 are listed.
- Videos land in a folder named after the playlist, numbered in order if your file name
  uses `{index}`.

### Convert options

These sit under the format list in the Convert dialog (`c`), depending on the video.

| Option | What it does |
| --- | --- |
| Clip | Keeps only a start and end time (`1:30`, `90` or `1m30s`). The cut happens while downloading, so you don't fetch the whole video. |
| Subtitles | Puts them inside the video (MP4, MKV, WebM) or in a separate `.srt`, in any languages (`en,de`). YouTube's automatic captions count. |
| SponsorBlock | YouTube only. Removes sponsor, self-promo and "like and subscribe" segments, or marks them as chapters. Uses [SponsorBlock](https://sponsor.ajay.app)'s community list. |
| One file per chapter | Splits a video with chapters, like an album upload, into numbered tracks. |
| Even out the volume | Audio only. Normalises loudness to -16 LUFS, the podcast standard. |

The dialog remembers these picks for next time. Press `o` to open the Options section.

**Pick a clip by eye.** The **pick** button on the Clip row opens a large frame and a
timeline:

| `←` `→` | `shift` + `←` `→` | `[` | `]` | `enter` |
| --- | --- | --- | --- | --- |
| Step | Jump 10 s | Mark start | Mark end | Use them |

On YouTube the frames come from the site's own scrub-bar previews, so nothing downloads.
For files on your computer, ffmpeg grabs them.

### Presets

- Set up the Convert dialog the way you like, then press **+ save**.
- Presets show as chips along the top of the dialog. Press `1` to `9` to apply one.
- Make a preset the default for a site, and `d` on that site uses it instead of best MP4.

### Following channels and playlists

- Press `a` and choose how new videos should be saved. Carrot checks for new ones each
  time it starts, and the status line tells you when something arrives.
- `w` lists everything you follow. `enter` gets one's new videos, `a` gets all of them,
  `s` checks now and `delete` unfollows.
- New videos land in a folder named after the channel.
- `carrot sync` does the same from cron or a scheduled task. `carrot sync --check` only
  reports what's new.

### Search what was said

- Carrot keeps the captions of every video you download, so you can search them.
- Press `/` and type. Words can be in any order. Use quotes for an exact phrase.
- Each hit shows when it was said. `enter` plays from that moment (in mpv, IINA or VLC
  if you have one), `c` cuts the 30 seconds around it into a new file, `o` shows the file.
- You can turn caption keeping off in Settings.

### Convert files on your computer

- Press `ctrl+t` for the Convert files tab. Drag in files, paste their paths, or press `f`
  to browse. Videos, audio and photos can be mixed.
- `c` picks a format. Video, Audio, Image, and Frames, which saves a video as a folder of
  pictures. Clip and volume options work here too.
- For images, set a max size in KB and Carrot lowers the quality until the file fits.
  You can also strip EXIF and location data before sharing.
- The new file lands next to the original, which Carrot never changes. If the streams
  already fit the new format, Carrot copies them instead of re-encoding. That takes
  seconds and loses nothing, and the dialog tells you which you're getting.
- `r` removes the highlighted file, `x` cancels a conversion.

## Command line

```sh
carrot                    # open the app
carrot <url>              # open with a link loaded
carrot <file>             # open Convert files with that file
carrot get <url>          # download without the app
carrot convert <file>     # convert without the app
carrot sync               # get new videos from what you follow
carrot setup              # download ffmpeg and list the formats it can make
carrot update             # update yt-dlp
```

`get` and `convert` print progress to stderr and each saved path to stdout, so you can
pipe the result into another command. They use the folder, file names and cookies from
the app's Settings.

```sh
carrot get URL --format mp3 --quality 320 --normalize
carrot get URL --start 1:30 --end 2:00 --format gif --quality 360
carrot get URL --subs srt --sub-langs en,de --sponsorblock remove
carrot get PLAYLIST_URL --format m4a --latest 10 --name '{index} - {title}'
carrot convert *.heic --format jpg --max-kb 500 --strip-metadata
mpv "$(carrot get URL --quiet)"
```

Run `carrot get --help` or `carrot convert --help` for every option.

## Formats

### Downloads

| Video | Audio |
| --- | --- |
| MP4, MKV, WebM, MOV, GIF | MP3, M4A, Opus, FLAC, WAV, OGG |
| best, 2160p to 360p | best, 320, 192, 128 kbps |

- The quality list only shows resolutions the video actually has.
- Files get the title, artist and cover art where the format allows.
- Carrot never re-encodes a download, except for GIF or MOV at a set height.

> [!NOTE]
> Best MP4, which `d` downloads, is H.264, so it plays in QuickTime and on phones and
> TVs. On YouTube that tops out at 1080p. Picking 1440p or 2160p MP4 copies YouTube's
> VP9 or AV1 video into the MP4 as is. That's fast and full quality, but some older
> players won't open it.

### Converting files

| Type | Formats |
| --- | --- |
| Video (15) | MP4, MKV, WebM, MOV, M4V, AVI, TS, FLV, WMV, MPG, 3GP, OGV, GIF, animated PNG, animated WebP |
| Audio (18) | MP3, M4A, AAC, Opus, OGG, FLAC, ALAC, WAV, AIFF, WMA, AC3, E-AC3, MP2, WavPack, TTA, CAF, AU, MKA |
| Image (18) | JPG, PNG, WebP, AVIF, JPEG XL, HEIC, JPEG 2000, TIFF, BMP, GIF, TGA, QOI, EXR, HDR, PPM, PCX, SGI, DPX |

## Settings and files

**File names.** Downloads go to your Downloads folder as `Title [video-id].ext`. In
Settings (`s`) you can change the folder and build a name from `{title}`, `{id}`,
`{channel}`, `{uploader}`, `{date}`, `{year}`, `{site}`, `{height}`, `{playlist}` and
`{index}`. A `/` makes a folder, so `{channel}/{date} {title}` files each video under its
channel. If the name is taken, Carrot adds ` (2)`.

**History.** `h` lists everything you've downloaded or converted. `enter` opens a file,
`o` shows it in its folder, `r` loads the link again to get another format, `y` copies the
link, and `delete` forgets the entry. The file stays.

**Themes.** Carrot and Carrot light, the defaults, use only the 256 standard terminal
colours, so they look the same in every terminal. Press `t` for the others.

**Updates.** Carrot checks PyPI while it's open. If there's a new release, it installs it
after you quit. Set `CARROT_NO_AUTO_UPDATE=1` to turn this off.

**Where things are kept.** Settings (`config.toml`), history (`history.jsonl`) and
captions (`transcripts/`) live in one folder, with Carrot's ffmpeg in an `ffmpeg/` folder:

- macOS: `~/Library/Application Support/carrot-leaf/`
- Linux: `~/.config/carrot-leaf/`, and ffmpeg in `~/.local/share/carrot-leaf/`
- Windows: `%LOCALAPPDATA%\carrot-leaf\`

## Troubleshooting

<details>
<summary><b>A video says I need to sign in</b></summary>

Age-restricted and members-only videos need a YouTube login. Carrot offers to borrow
cookies from a browser where you're signed in: Chrome, Firefox, Safari, Edge, Brave,
Opera, Vivaldi or Chromium. Cookies stay off until you choose a browser, and you can turn
them off in Settings. On a Mac, expect a Keychain prompt the first time.

</details>

<details>
<summary><b>YouTube asks me to prove I'm not a bot</b></summary>

That comes from many requests on one connection, not from the video. It usually clears
within an hour. Browser cookies (see above) get you past it right away.

</details>

<details>
<summary><b>Downloads stopped working</b></summary>

Sites change how they serve video, and yt-dlp ships fixes every few weeks. Carrot checks
for a new yt-dlp at startup and tells you when one is out. Press `u` or run
`carrot update`.

</details>

<details>
<summary><b>A format is greyed out</b></summary>

The dialog shows what that format needs. Carrot uses your own ffmpeg where it can and its
bundled build for the rest (Homebrew's ffmpeg has no WebP or Theora, for example). JPEG XL
and HEIC need libjxl's `cjxl` and libheif's `heif-enc`:

```sh
brew install jpeg-xl libheif
```

</details>

## Uninstall

```sh
uv tool uninstall carrot-leaf     # or: pipx uninstall carrot-leaf
```

Carrot's ffmpeg is kept outside the app, so updates don't download it again. To remove
it too, delete the `ffmpeg` folder listed in [Settings and files](#settings-and-files).

## Development

```sh
git clone https://github.com/Yore-Des/carrot-leaf && cd carrot-leaf/rust
cargo run --release            # run the app
cargo test && cargo clippy
cd .. && uv build              # build the PyPI wheel, as users get it
```

The app is the Rust program in `rust/`, built with [ratatui](https://ratatui.rs) on top of
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and [ffmpeg](https://ffmpeg.org). It ships on
PyPI as a wheel holding the `carrot` binary, with yt-dlp and Deno as dependencies. The
original Python version lives at the `python-final` tag.

**Releasing.** Bump `version` in `pyproject.toml` and `rust/Cargo.toml`, then push a
`vX.Y.Z` tag. GitHub Actions builds the wheels and publishes them to PyPI.

## License

MIT. Only download videos you have the right to, and follow each site's terms.
