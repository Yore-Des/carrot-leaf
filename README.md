<pre>
 ██████╗ █████╗ ██████╗ ██████╗  ██████╗ ████████╗
██╔════╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗╚══██╔══╝
██║     ███████║██████╔╝██████╔╝██║   ██║   ██║
██║     ██╔══██║██╔══██╗██╔══██╗██║   ██║   ██║
╚██████╗██║  ██║██║  ██║██║  ██║╚██████╔╝   ██║
 ╚═════╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝    ╚═╝
</pre>

Carrot downloads and converts videos in your terminal. Paste a link from YouTube or
any of the 1,300+ sites yt-dlp knows, then press `d` to save it as MP4 or `c` to pick a
format. It runs on [yt-dlp](https://github.com/yt-dlp/yt-dlp) and
[Textual](https://textual.textualize.io/).

## Install

macOS and Linux:

```sh
curl -LsSf https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.sh | sh
```

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://raw.githubusercontent.com/Yore-Des/carrot-leaf/main/install.ps1 | iex"
```

The script installs [uv](https://docs.astral.sh/uv/) if it's missing, then installs Carrot
Leaf with its own copy of Python. You don't need Python, ffmpeg or anything else set up
first.

If you already use uv or pipx:

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

Paste a link and the title, length, upload date and a thumbnail appear. The bottom bar
lists every key:

| Key   | Does                                                       |
| ----- | ---------------------------------------------------------- |
| `d`   | Download at the best quality, as MP4                       |
| `c`   | Convert: pick a video or audio format and a quality        |
| `l`   | List supported sites. Search by name, or paste a link to check it |
| `s`   | Settings: download folder and browser cookies              |
| `t`   | Pick a theme                                               |
| `o`   | Show the last download in Finder or Explorer               |
| `u`   | Update yt-dlp                                              |
| `tab` | Leave the URL bar so the letter keys work                  |
| `esc` | Back to the URL bar                                        |
| `q`   | Quit                                                       |

While the URL bar has focus, letters go into the link. Press `tab` first, or click a key
in the bottom bar.

You can paste the next link while a download runs. Downloads queue up and run one at a
time.

### Formats

| Video                     | Audio                              |
| ------------------------- | ---------------------------------- |
| MP4, MKV, WebM, MOV, GIF  | MP3, M4A, Opus, FLAC, WAV, OGG     |
| best, 2160p … 360p        | best, 320 / 192 / 128 kbps         |

The quality list only shows resolutions the video has. Where the format allows it, the
file gets the title, artist and cover art.

### Where files go

Downloads land in your Downloads folder as `Title [video-id].ext`. Press `s` to pick
another folder. If a file with that name already exists, the new one becomes
`Title [video-id] (2).ext`.

Settings, including your theme, live in:

- macOS: `~/Library/Application Support/carrot-leaf/config.toml`
- Linux: `~/.config/carrot-leaf/config.toml`
- Windows: `%LOCALAPPDATA%\carrot-leaf\config.toml`

### Themes

Carrot and Carrot light are the defaults. Both use only the 256 standard terminal
colours, so they look the same in macOS Terminal as in iTerm2, Ghostty or Kitty. Press
`t` to try the others. The app remembers your pick.

### Videos that need you signed in

Age-restricted and members-only videos need a YouTube login. When Carrot Leaf hits one,
it offers to borrow the cookies from a browser where you're signed in: Chrome, Firefox,
Safari, Edge, Brave, Opera, Vivaldi or Chromium. Cookies stay off until you pick a
browser, and you can switch them off again in Settings. On a Mac, expect a Keychain
prompt the first time.

YouTube sometimes asks you to prove you're not a bot. That happens after a lot of
requests from one connection, not because of the video, and it usually clears within an
hour. Browser cookies get you past it right away.

### When downloads break

Sites change how they serve video, YouTube more than most, and yt-dlp ships a fix every
few weeks. Carrot Leaf checks for a new yt-dlp at startup and tells you when one is out.
Press `u` or run `carrot update` to install it.

### Updates

Carrot Leaf updates itself. While it's open, it checks PyPI for a newer release. If it
finds one, it installs it in the background after you quit, and your next launch runs
the new version. This works for the install script, `uv tool` and `pipx`. Set
`CARROT_NO_AUTO_UPDATE=1` to turn it off.

### ffmpeg and Deno

Converting needs ffmpeg, and YouTube needs Deno to run its JavaScript. Carrot Leaf uses
your own copies if you have them. If not, Deno ships with the package, and ffmpeg
downloads once (about 70 MB) the first time a conversion needs it.

## Uninstall

```sh
uv tool uninstall carrot-leaf     # or: pipx uninstall carrot-leaf
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

To release, bump `version` in `pyproject.toml` and push a `vX.Y.Z` tag. GitHub Actions
builds it and publishes to PyPI.

## Please be nice

Only download videos you have the right to download, and follow each site's terms.

## License

MIT
