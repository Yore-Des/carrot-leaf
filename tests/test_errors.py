import pytest

from carrot_leaf.engine import errors


@pytest.mark.parametrize(
    "text, kind",
    [
        (
            "ERROR: [youtube] abc: Sign in to confirm your age. This video may be inappropriate",
            errors.LOGIN,
        ),
        (
            "ERROR: [youtube] abc: Join this channel to get access to members-only content",
            errors.LOGIN,
        ),
        ("ERROR: [youtube] abc: Sign in to confirm you’re not a bot", errors.LOGIN),
        (
            "ERROR: [youtube] abc: Private video. Sign in if you've been granted access",
            errors.UNAVAILABLE,
        ),
        ("ERROR: [youtube] abc: Video unavailable", errors.UNAVAILABLE),
        ("ERROR: Unsupported URL: https://example.com/", errors.UNSUPPORTED),
        (
            "ERROR: Unable to download webpage: <urlopen error [Errno 8] nodename nor servname provided>",
            errors.NETWORK,
        ),
        ("ERROR: unable to download video data: HTTP Error 403: Forbidden", errors.OUTDATED),
        ("ERROR: [youtube] abc: Requested format is not available", errors.OUTDATED),
        ("That's a playlist or channel. Paste a link to a single video.", errors.PLAYLIST),
    ],
)
def test_kinds(text, kind):
    assert errors.explain(text).kind == kind


def test_generic_strips_prefix():
    e = errors.explain("ERROR: [generic] xyz: Something odd happened\nmore")
    assert e.kind == errors.GENERIC
    assert e.message == "Something odd happened"
