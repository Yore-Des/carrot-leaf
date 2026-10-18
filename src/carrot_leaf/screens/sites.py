"""Supported sites: search yt-dlp's site list, or paste a link to check it."""

from __future__ import annotations

from functools import lru_cache

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


@lru_cache(maxsize=1)
def extractors() -> tuple:
    from yt_dlp.extractor import gen_extractor_classes

    return tuple(c for c in gen_extractor_classes() if c.IE_NAME != "generic")


@lru_cache(maxsize=1)
def sites() -> list[tuple[str, bool]]:
    """(site name, works) per site. "youtube:tab" and friends fold into "youtube"."""
    works: dict[str, bool] = {}
    for ie in extractors():
        name = ie.IE_NAME.split(":")[0]
        works[name] = works.get(name, False) or ie.working()
    return sorted(works.items(), key=lambda kv: kv[0].lower())


def check_url(url: str) -> list[str]:
    """Names of the extractors that handle `url`, best match first."""
    url = url.strip()
    if "://" not in url:
        url = "https://" + url
    return [ie.IE_NAME for ie in extractors() if ie.suitable(url)]


def looks_like_link(text: str) -> bool:
    text = text.strip()
    return "/" in text or text.startswith(("http:", "https:", "www."))


class SitesScreen(ModalScreen[None]):
    BINDINGS = [Binding("escape", "dismiss", "Close")]

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="sites"):
            yield Static(id="dialog-title")
            yield Input(placeholder="Search sites, or paste a link to check it", id="site-search")
            yield Static(id="site-result")
            yield OptionList(id="sites")

    def on_mount(self) -> None:
        total = len(sites())
        self.query_one("#dialog-title", Static).update(
            f"[b]Supported sites[/b]  [dim]{total:,} sites via yt-dlp[/dim]"
        )
        self._show("")
        self.query_one("#site-search", Input).focus()

    @on(Input.Changed, "#site-search")
    def _search(self, event: Input.Changed) -> None:
        self._show(event.value)

    def _show(self, query: str) -> None:
        result = self.query_one("#site-result", Static)
        options = self.query_one("#sites", OptionList)
        query = query.strip()
        if looks_like_link(query):
            matches = check_url(query)
            if matches:
                result.update(f"[b $success]✓ Supported[/]  [dim]by[/dim] {escape(matches[0])}")
            else:
                result.update(
                    "[b $warning]No dedicated support.[/] "
                    "[dim]Carrot Leaf will still try: many pages with a plain video work.[/dim]"
                )
            names = {m.split(":")[0] for m in matches}
            rows = [(n, ok) for n, ok in sites() if n in names]
        else:
            low = query.lower()
            rows = [(n, ok) for n, ok in sites() if low in n.lower()]
            if not query:
                result.update(
                    "[dim]Type to filter. Sites marked broken may not work right now.[/dim]"
                )
            elif rows:
                result.update(f"[dim]{len(rows):,} match{'es' if len(rows) != 1 else ''}[/dim]")
            else:
                result.update(
                    f"[dim]No site called “{escape(query)}”. Paste a link to check it directly.[/dim]"
                )
        options.clear_options()
        options.add_options(
            Option(escape(n) if ok else f"{escape(n)}  [dim]broken[/dim]", id=n) for n, ok in rows
        )
