"""/youtube/posts/* — one community post and its comments. A post's browse
params need its channel id, which only the post page says, so the first
call reads https://www.youtube.com/post/<id> (ytInitialData)."""
from youtube import items as I
from youtube import refs
from youtube import social_parsers as S
from youtube import values as V
from youtube.fetch import YoutubeBadRequest, YoutubeNotFound, innertube, page_initial_data
from youtube.shared import BROWSE_CURSOR_PREFIX, locale, page_result


def _post_page(post, hl, gl):
    try:
        data = page_initial_data(f"/post/{post}", hl=hl, gl=gl)
    except YoutubeNotFound:
        raise YoutubeNotFound(f"post '{post}' not found")
    rows = S.posts_in(data.get("contents") or data)
    row = next((r for r in rows if r["id"] == post), rows[0] if rows else None)
    if row is None:
        raise YoutubeNotFound(f"post '{post}' not found")
    return data, row


def details(post, country="US", language="en"):
    """Text, links, attachment (images, poll, quiz, linked video / playlist),
    like and comment counts and the author channel."""
    hl, gl = locale(country, language)
    _, row = _post_page(post, hl, gl)
    row.pop("detail_params", None)
    return {"post": row}


def _sort_token(first_page, sort):
    menu = V.first(first_page, "sortFilterSubMenuRenderer") or {}
    items = menu.get("subMenuItems") or []
    index = 1 if sort == "newest" else 0
    if len(items) > index:
        command = V.first(items[index], "continuationCommand")
        if isinstance(command, dict):
            return command.get("token")
    return None


def comments(post, sort="top", cursor=None, country="US", language="en"):
    """Top or newest comments under a post, 20 a page (the first page
    carries the exact total). replies_cursor feeds /comments/replies."""
    hl, gl = locale(country, language)
    link = refs.post_link(post)
    first_total = None
    if cursor:
        try:
            data = innertube("browse", {"continuation": cursor}, hl=hl, gl=gl)
        except YoutubeBadRequest:
            raise YoutubeBadRequest("cursor is not a valid post comments cursor")
    else:
        page, _ = _post_page(post, hl, gl)
        token = I.next_cursor(page.get("contents") or {})
        if not token:
            return page_result("comments", [], None, post={"id": post, "link": link},
                               are_comments_enabled=False, total_count=None, sort=sort)
        data = innertube("browse", {"continuation": token}, hl=hl, gl=gl)
        if sort == "newest":
            newest = _sort_token(data, "newest")
            if newest:
                first_total = S.comments_page(data)[0]
                data = innertube("browse", {"continuation": newest}, hl=hl, gl=gl)
    total, rows, next_cursor = S.comments_page(data, link_base=link)
    if total is None:
        total = first_total
    for row in rows:
        if row.get("replies_cursor"):
            row["replies_cursor"] = BROWSE_CURSOR_PREFIX + row["replies_cursor"]
    return page_result("comments", rows, next_cursor, post={"id": post, "link": link},
                       are_comments_enabled=True, total_count=total, sort=sort)
