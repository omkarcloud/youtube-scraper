"""Use the scraper straight from Python — no server needed.

    python main.py

Every function returns the same JSON the API does; results are written to
output/*.json. A video can be an ID or any YouTube video link; a channel an
@handle, a channel ID or any channel link.
"""
import json
import os

from youtube import channels, refs, search, videos

os.makedirs("output", exist_ok=True)


def save(name, data):
    path = os.path.join("output", name)
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"saved {path}")


if __name__ == "__main__":
    # everything about a video: exact views and likes, chapters, heatmap, music, captions
    save("video_despacito.json", videos.details(refs.resolve_video("https://www.youtube.com/watch?v=kJQP7kiw5Fk")))

    # ~20 results a page, with YouTube's own filters
    save("search_raspberry_pi.json", search.search("raspberry pi", type="video", sort="popularity"))

    # the full channel profile: exact subscribers, views, joined date, links
    save("channel_mrbeast.json", channels.details(refs.resolve_channel("@MrBeast")))
