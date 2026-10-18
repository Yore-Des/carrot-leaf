<pre>
 ██████  █████  ██████  ██████   ██████  ████████
██▓▓▓▓▓▓██▓▓▓██ ██▓▓▓██ ██▓▓▓██ ██▓▓▓▓██  ▓▓██▓▓▓▓
██▓░░░░░███████▓██████▓▓██████▓▓██▓░░░██▓  ░██▓░░░░
██▓░    ██▓▓▓██▓██▓▓▓██░██▓▓▓██░██▓░  ██▓░  ██▓░
 ██████ ██▓░░██▓██▓░░██▓██▓░░██▓ ██████▓▓░  ██▓░
  ▓▓▓▓▓▓ ▓▓░  ▓▓░▓▓░  ▓▓░▓▓░  ▓▓░ ▓▓▓▓▓▓░░   ▓▓░
   ░░░░░░ ░░   ░░ ░░   ░░ ░░   ░░  ░░░░░░     ░░
</pre>

A good-looking terminal app for downloading and converting videos from YouTube and
1,000+ other sites. Paste a link, then **Download** or **Convert**. Built on
[yt-dlp](https://github.com/yt-dlp/yt-dlp) and [Textual](https://textual.textualize.io/).

## Install

**macOS / Linux**

```sh
curl -LsSf https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.sh | sh
```

**Windows** (PowerShell)

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.ps1 | iex"
```

The script installs [uv](https://docs.astral.sh/uv/) if you don't have it, then installs
Carrot Leaf with its own Python. Your system Python doesn't matter, and you don't need to
install ffmpeg or anything else.

Already use uv or pipx?

```sh
uv tool install carrot-leaf
pipx install carrot-leaf      # needs Python 3.10+
```

## Use it

```sh
carrot                 # open the app
carrot <url>           # open with a link already loaded
carrot update          # update yt-dlp
carrot --version
```

Paste a link anywhere in the app. The video's details and thumbnail show up, then:

| Key   | Does                                                    |
| ----- | ------------------------------------------------------- |
| `d`   | **Download**: best quality, saved as MP4                |
| `c`   | **Convert**: pick a video or audio format and a quality |
| `s`   | Settings: download folder, browser cookies              |
| `o`   | Show the last downloaded file in Finder / Explorer      |
| `u`   | Update yt-dlp                                           |
| `esc` | Back to the URL bar                                     |
| `q`   | Quit                                                    |

You can paste another link while a download is running. Jobs wait in line and run one
at a time.

### Formats

| Video                     | Audio                              |
| ------------------------- | ---------------------------------- |
| MP4, MKV, WebM, MOV, GIF  | MP3, M4A, Opus, FLAC, WAV, OGG     |
| best, 2160p … 360p        | best, 320 / 192 / 128 kbps         |

Carrot Leaf only offers resolutions the video actually has. Files get the title, artist
and cover art embedded where the format supports it.

### Where files go

Files are saved to your Downloads folder as `Title [video-id].ext`. Change the folder with
`s`. If a file with the same name exists, the new one is saved as `Title [id] (2).ext`.

Settings are stored in:

- macOS: `~/Library/Application Support/carrot-leaf/config.toml`
- Linux: `~/.config/carrot-leaf/config.toml`
- Windows: `%LOCALAPPDATA%\carrot-leaf\config.toml`

### Age-restricted and members-only videos

These need you to be signed in. When Carrot Leaf hits one, it offers to use the cookies
from a browser where you're signed in (Chrome, Firefox, Safari, Edge, Brave, …). This is
off until you pick a browser, and you can turn it off again in Settings. On a Mac you may
see a Keychain prompt the first time.

### When downloads break

Sites change often, and YouTube most of all, so yt-dlp ships fixes every few weeks.
Carrot Leaf checks for a new yt-dlp when it starts and shows a notice if one is out.
Press `u` or run `carrot update`.

### ffmpeg and Deno

Converting needs ffmpeg, and YouTube needs a JavaScript runtime (Deno). If they're
already installed, Carrot Leaf uses them. If not, it uses bundled copies: Deno comes with
the package, and ffmpeg is downloaded once (about 70 MB) the first time it's needed.

## Uninstall

```sh
uv tool uninstall carrot-leaf
```

## Development

```sh
git clone https://github.com/Yore-Des/carrot-leaf && cd carrot-leaf
uv sync
uv run carrot
uv run pytest                       # offline tests
CARROT_LIVE=1 uv run pytest         # also run real downloads
uv run ruff check src tests && uv run ruff format src tests
```

Releases: bump `version` in `pyproject.toml`, then push a `vX.Y.Z` tag. GitHub Actions
publishes to PyPI.

## Please be nice

Only download videos you have the right to download, and respect each site's terms.

## License

MIT
