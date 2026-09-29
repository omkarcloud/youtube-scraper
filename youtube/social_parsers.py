"""Comment, live-chat and community-post normalizers.

Comments arrive split in two: the thread renderers only carry keys, the
text / author / counters live in `frameworkUpdates` entity mutations
(commentEntityPayload, engagementToolbarStateEntityPayload) — joined here
by key. Counts on comments are YouTube's display values ("86K").

Dropped as noise: the entity keys themselves, surface/shared payloads
(UI strings such as "Read more"), translate-button keys, like/dislike
tooltips, logging directives, live-chat context menus and placeholder
items, the poll sign-in endpoints (results are login-only).
"""
import re

from youtube import refs
from youtube import values as V
from youtube.items import channel_ref, handle_of

_EDITED_RE = re.compile(r"\s*\(edited\)\s*$", re.I)


def entity_map(data):
    """frameworkUpdates mutations -> {entity key: payload body}."""
    out = {}
    for mutation in V.find_key(data, "frameworkUpdates", "entityBatchUpdate", "mutations") or []:
        if not isinstance(mutation, dict):
            continue
        payload = mutation.get("payload") or {}
        for body in payload.values():
            if isinstance(body, dict):
                out[mutation.get("entityKey") or body.get("key")] = body
    return out


def _author(author):
    author = author or {}
    path = V.find_key(author, "channelCommand", "innertubeCommand", "browseEndpoint", "canonicalBaseUrl")
    cid = author.get("channelId")
    name = V.clean(author.get("displayName"))
    handle = handle_of(path) or (name if name and name.startswith("@") else None)
    return {
        "id": cid,
        "name": name,
        "handle": handle,
        "link": refs.channel_link(cid, handle),
        "thumbnail": refs.absolute(author.get("avatarThumbnailUrl")),
        "is_verified": bool(author.get("isVerified")),
        "is_artist": bool(author.get("isArtist")),
        "is_creator": bool(author.get("isCreator")),
    }


def _replies_cursor(thread):
    renderer = V.find_key(thread, "replies", "commentRepliesRenderer") or {}
    for command in V.walk(renderer, "continuationCommand"):
        if isinstance(command, dict) and command.get("token"):
            return command["token"]
    return None


def comment(view_model, entities, *, link_base=None, replies_cursor=None):
    """commentViewModel + its entities -> one comment."""
    view_model = view_model or {}
    payload = entities.get(view_model.get("commentKey")) or {}
    props = payload.get("properties") or {}
    toolbar = payload.get("toolbar") or {}
    state = entities.get(view_model.get("toolbarStateKey")) or {}
    comment_id = props.get("commentId") or view_model.get("commentId")
    if not comment_id:
        return None
    published = V.clean(props.get("publishedTime")) or ""
    likes = toolbar.get("likeCountNotliked")
    link = f"{link_base}{'&' if '?' in link_base else '?'}lc={comment_id}" if link_base else None
    return {
        "id": comment_id,
        "text": V.text(props.get("content")),
        "link": link,
        "published_time_text": _EDITED_RE.sub("", published) or None,
        "is_edited": bool(_EDITED_RE.search(published)),
        "like_count": V.count(likes) if V.clean(likes) else 0,
        "reply_count": V.count(toolbar.get("replyCount")) if V.clean(toolbar.get("replyCount")) else 0,
        "is_pinned": bool(view_model.get("pinnedText")),
        "is_hearted": state.get("heartState") == "TOOLBAR_HEART_STATE_HEARTED",
        "reply_level": V.to_int(props.get("replyLevel")) or 0,
        "author": _author(payload.get("author")),
        "replies_cursor": replies_cursor,
    }


def comments_page(data, link_base=None):
    """A comments continuation response -> (total_count, comments, next_cursor).
    Works for the first page (with the header count), later pages and reply
    pages. `link_base` is the video / post link each comment's `link` hangs off."""
    entities = entity_map(data)
    total = None
    rows = []
    items = []
    for key in ("onResponseReceivedEndpoints", "onResponseReceivedActions"):
        for action in data.get(key) or []:
            if not isinstance(action, dict):
                continue
            for inner in action.values():
                if isinstance(inner, dict) and isinstance(inner.get("continuationItems"), list):
                    items.extend(inner["continuationItems"])
    cursor = None
    for item in items:
        if not isinstance(item, dict):
            continue
        if "commentsHeaderRenderer" in item:
            header = item["commentsHeaderRenderer"]
            total = V.count(V.text(header.get("countText")) or V.text(header.get("commentsCount")))
        elif "commentThreadRenderer" in item:
            thread = item["commentThreadRenderer"]
            view_model = V.find_key(thread, "commentViewModel", "commentViewModel") or thread.get("commentViewModel")
            row = comment(view_model, entities, link_base=link_base, replies_cursor=_replies_cursor(thread))
            if row:
                rows.append(row)
        elif "commentViewModel" in item:
            row = comment(item["commentViewModel"], entities, link_base=link_base)
            if row:
                rows.append(row)
        elif "continuationItemRenderer" in item:
            for command in V.walk(item["continuationItemRenderer"], "continuationCommand"):
                if isinstance(command, dict) and command.get("token"):
                    cursor = command["token"]
                    break
    return total, rows, cursor


# ---- live chat ------------------------------------------------------------------------------------

_CURRENCY_SYMBOLS = {
    "$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "JP¥": "JPY", "₹": "INR", "₩": "KRW",
    "CA$": "CAD", "A$": "AUD", "AU$": "AUD", "NZ$": "NZD", "MX$": "MXN", "R$": "BRL", "HK$": "HKD",
    "NT$": "TWD", "₱": "PHP", "₫": "VND", "₺": "TRY", "₪": "ILS", "CHF": "CHF", "₽": "RUB", "zł": "PLN",
    "kr": "SEK", "R": "ZAR", "S$": "SGD", "฿": "THB", "RM": "MYR", "Rp": "IDR", "CN¥": "CNY",
}
_AMOUNT_RE = re.compile(r"^\s*([^\d\s.,]*)\s*([\d.,]+)\s*([^\d\s.,]*)\s*$")


def money(label):
    """'$5.00' -> {amount: 5.0, currency: 'USD', text: '$5.00'}; 'CA$10' -> CAD;
    '₹1,000.00' -> INR; unknown symbols keep currency null."""
    s = V.clean(label)
    if not s:
        return None
    match = _AMOUNT_RE.match(s)
    if not match:
        return {"amount": None, "currency": None, "text": s}
    prefix, number, suffix = match.groups()
    symbol = (prefix or suffix or "").strip()
    currency = _CURRENCY_SYMBOLS.get(symbol) or (symbol.upper() if re.fullmatch(r"[A-Za-z]{3}", symbol or "") else None)
    if "," in number and ("." not in number or number.rfind(",") > number.rfind(".")) \
            and len(number) - number.rfind(",") - 1 in (1, 2):
        number = number.replace(".", "").replace(",", ".")      # 1.000,00 -> 1000.00
    try:
        amount = float(number.replace(",", ""))
    except ValueError:
        amount = None
    return {"amount": amount, "currency": currency, "text": s}


def _message_text(message):
    """Live chat message runs -> text (emoji rendered as their shortcut / emoji)."""
    if not isinstance(message, dict):
        return None
    out = []
    for run in message.get("runs") or []:
        if not isinstance(run, dict):
            continue
        if "text" in run:
            out.append(str(run["text"]))
        elif isinstance(run.get("emoji"), dict):
            emoji = run["emoji"]
            shortcuts = emoji.get("shortcuts") or []
            out.append(emoji.get("emojiId") if not emoji.get("isCustomEmoji") else (shortcuts[0] if shortcuts else ""))
    return V.clean("".join(out))


def _chat_author(renderer):
    roles = set()
    member_badge = None
    for badge in renderer.get("authorBadges") or []:
        badge_renderer = badge.get("liveChatAuthorBadgeRenderer") if isinstance(badge, dict) else None
        if not isinstance(badge_renderer, dict):
            continue
        icon = V.find_key(badge_renderer, "icon", "iconType")
        if icon:
            roles.add(str(icon).upper())
        elif badge_renderer.get("customThumbnail"):
            roles.add("MEMBER")
            member_badge = V.clean(badge_renderer.get("tooltip"))
    cid = renderer.get("authorExternalChannelId")
    name = V.text(renderer.get("authorName"))
    return {
        "id": cid,
        "name": name,
        "handle": name if name and name.startswith("@") else None,
        "link": refs.channel_link(cid, name if name and name.startswith("@") else None),
        "thumbnail": V.best_thumbnail(renderer.get("authorPhoto")),
        "is_owner": "OWNER" in roles,
        "is_moderator": "MODERATOR" in roles,
        "is_verified": "VERIFIED" in roles,
        "is_member": "MEMBER" in roles,
        "member_badge": member_badge,
    }


_CHAT_TYPES = {
    "liveChatTextMessageRenderer": "text",
    "liveChatPaidMessageRenderer": "super_chat",
    "liveChatPaidStickerRenderer": "super_sticker",
    "liveChatMembershipItemRenderer": "membership",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": "gift_purchase",
    "liveChatSponsorshipsGiftRedemptionAnnouncementRenderer": "gift_redemption",
}


def chat_message(key, renderer, offset_ms=None):
    kind = _CHAT_TYPES.get(key)
    if not kind or not isinstance(renderer, dict) or not renderer.get("id"):
        return None
    if kind == "gift_purchase":
        header = V.find_key(renderer, "header", "liveChatSponsorshipsHeaderRenderer") or {}
        text = V.text(header.get("primaryText"))
        author = _chat_author(dict(header, authorExternalChannelId=renderer.get("authorExternalChannelId")))
    else:
        text = _message_text(renderer.get("message")) or V.text(renderer.get("headerSubtext")) or \
            V.text(renderer.get("headerPrimaryText"))
        author = _chat_author(renderer)
    out = {
        "id": renderer["id"],
        "type": kind,
        "text": text,
        "published_at": V.iso_from_usec(renderer.get("timestampUsec")),
        "video_offset_seconds": round(int(offset_ms) / 1000, 3) if V.to_int(offset_ms) is not None else None,
        "amount": money(V.text(renderer.get("purchaseAmountText"))) if kind in ("super_chat", "super_sticker") else None,
        "sticker": V.best_thumbnail(renderer.get("sticker")) if kind == "super_sticker" else None,
        "author": author,
    }
    return out


def chat_page(data):
    """get_live_chat / get_live_chat_replay -> (messages, next_cursor, poll_after_ms)."""
    continuation = V.find_key(data, "continuationContents", "liveChatContinuation") or {}
    actions = continuation.get("actions") or []
    messages = []

    def take(action, offset=None):
        item = V.find_key(action, "addChatItemAction", "item") or {}
        for key, renderer in item.items():
            msg = chat_message(key, renderer, offset)
            if msg:
                messages.append(msg)

    for action in actions:
        if not isinstance(action, dict):
            continue
        replay = action.get("replayChatItemAction")
        if isinstance(replay, dict):
            for inner in replay.get("actions") or []:
                take(inner, replay.get("videoOffsetTimeMsec"))
        else:
            take(action)
    cursor, wait = None, None
    for entry in continuation.get("continuations") or []:
        if not isinstance(entry, dict):
            continue
        for body in entry.values():
            if isinstance(body, dict) and body.get("continuation"):
                cursor = body["continuation"]
                wait = V.to_int(body.get("timeoutMs"))
                break
        if cursor:
            break
    return messages, cursor, wait


# ---- community posts ------------------------------------------------------------------------------

def _post_attachment(attachment):
    attachment = attachment or {}
    if not attachment:
        return None
    from youtube.items import collect
    if "backstageImageRenderer" in attachment:
        return {"type": "image", "images": [V.best_thumbnail(V.find_key(attachment, "backstageImageRenderer", "image"))]}
    if "postMultiImageRenderer" in attachment:
        images = [V.best_thumbnail(V.find_key(img, "backstageImageRenderer", "image"))
                  for img in V.find_key(attachment, "postMultiImageRenderer", "images") or []]
        return {"type": "images", "images": [i for i in images if i]}
    if "pollRenderer" in attachment:
        poll = attachment["pollRenderer"]
        choices = []
        for choice in poll.get("choices") or []:
            if isinstance(choice, dict):
                entry = {"text": V.text(choice.get("text")),
                         "image": V.best_thumbnail(V.find_key(choice, "image"))}
                ratio = V.to_float(choice.get("voteRatio"))
                entry["vote_percent"] = round(ratio * 100, 1) if ratio is not None else None
                choices.append(entry)
        return {"type": "poll", "poll": {"total_votes": V.count(V.text(poll.get("totalVotes"))),
                                         "kind": str(poll.get("type") or "").replace("POLL_TYPE_", "").lower() or None,
                                         "choices": choices}}
    if "quizRenderer" in attachment:
        quiz = attachment["quizRenderer"]
        return {"type": "quiz", "quiz": {"total_votes": V.count(V.text(quiz.get("totalVotes"))),
                                         "choices": [{"text": V.text(c.get("text")), "is_correct": c.get("isCorrect")}
                                                     for c in quiz.get("choices") or [] if isinstance(c, dict)]}}
    linked = collect(attachment)
    if linked:
        return {"type": linked[0]["type"], linked[0]["type"]: linked[0]}
    return None


def post(renderer):
    """backstagePostRenderer / postRenderer -> one community post."""
    renderer = renderer or {}
    pid = renderer.get("postId")
    if not pid:
        return None
    author_path = V.canonical_path(renderer.get("authorEndpoint"))
    author = channel_ref(V.text(renderer.get("authorText")), V.browse_id(renderer.get("authorEndpoint")),
                         author_path, V.best_thumbnail(renderer.get("authorThumbnail")))
    likes_a11y = next((label for label in V.walk(renderer.get("actionButtons"), "label")
                       if isinstance(label, str) and "along with" in label), None)
    like_count = V.count(likes_a11y.split("along with", 1)[1]) if likes_a11y else V.count(V.text(renderer.get("voteCount")))
    reply = V.find_key(renderer, "actionButtons", "commentActionButtonsRenderer", "replyButton", "buttonRenderer")
    links = []
    for run in V.find_key(renderer, "contentText", "runs") or []:
        if isinstance(run, dict) and run.get("navigationEndpoint"):
            links.append({"text": V.clean(run.get("text")), "link": V.nav_link(run["navigationEndpoint"])})
    return {
        "type": "post",
        "id": pid,
        "link": refs.post_link(pid),
        "text": V.text(renderer.get("contentText")),
        "published_time_text": V.text(renderer.get("publishedTimeText")),
        "like_count": like_count if like_count is not None else 0,
        "comment_count": V.count(V.text(reply.get("text"))) if isinstance(reply, dict) and V.text(reply.get("text")) else 0,
        "links": links,
        "attachment": _post_attachment(renderer.get("backstageAttachment")),
        "author": author,
        "detail_params": V.find_key(reply, "navigationEndpoint", "browseEndpoint", "params") if isinstance(reply, dict) else None,
    }


def posts_in(node):
    """Every post renderer in a response (Posts tab / post page)."""
    out, seen = [], set()
    for key in ("backstagePostRenderer", "postRenderer", "sharedPostRenderer"):
        for renderer in V.walk(node, key):
            if key == "sharedPostRenderer" and isinstance(renderer, dict):
                renderer = dict(renderer.get("originalPost", {}).get("backstagePostRenderer") or {},
                                postId=renderer.get("postId"))
            row = post(renderer)
            if row and row["id"] not in seen:
                seen.add(row["id"])
                out.append(row)
    return out
