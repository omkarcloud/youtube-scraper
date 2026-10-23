# 🎬 YouTube Scraper

YouTube Scraper is a **free and open-source** scraper that gets you **unlimited** detailed YouTube data for free.

## ✨ What Can I Get?

- 🎬 **Full details on any video — even Despacito's 9.1B views** — exact views & likes, chapters, most-replayed heatmap, music credits & captions
- 🔍 **Search the 500+ hours uploaded every minute** — videos, Shorts, channels, playlists & movies with YouTube's own filters
- 📺 **Channels up to MrBeast's 518M subscribers** — exact subscribers, views & joined date, plus videos, Shorts, live, playlists & posts
- 💬 **605K+ comments on Shake It Off alone** — every comment & reply, transcripts, subtitles, live chat & YouTube Charts

## 🎥 Example: A Full YouTube Video

```json
{
  "video": {
    "id": "kJQP7kiw5Fk",
    "title": "Luis Fonsi - Despacito ft. Daddy Yankee",
    "link": "https://www.youtube.com/watch?v=kJQP7kiw5Fk",
    "thumbnail": "https://i.ytimg.com/vi/kJQP7kiw5Fk/maxresdefault.jpg",
    "description": "“Despacito” disponible ya en todas las plataformas digitales: https://UMLE.lnk.to/DOoUzFp \n“Imposible” disponible ya...",
    "duration_seconds": 282,
    "category": "Music",
    "hashtags": ["Despacito", "LuisFonsi", "EchamelaCulpa"],
    "published_at": "2017-01-13T05:00:02Z",
    "view_count": 9132930752,
    "like_count": 56707084,
    "comment_count": 4300000,
    "has_captions": true,
    "channel": {"id": "UCxoq-PAQeAdk_zyg8YS0JqA", "name": "Luis Fonsi", "handle": "@LuisFonsiVEVO", "subscriber_count": 32600000, "is_verified": true, "is_artist": true},
    "music": [
      {"title": "Despacito", "artist": "Luis Fonsi", "album": "Despacito"}
    ],
    "heatmap": [
      {"start_seconds": 0.0, "duration_seconds": 2.8, "intensity": 0.6978},
      {"start_seconds": 2.8, "duration_seconds": 2.8, "intensity": 0.4731}
    ],
    "subtitles": [
      {"language_code": "en", "language": "English - en", "is_auto_generated": false},
      {"language_code": "es", "language": "Spanish", "is_auto_generated": false}
    ],
    "end_screen": [
      {"type": "playlist", "id": "PL9tY0BWXOZFtc4Kan4Q0HApZcdRuXN0YA", "title": "Sabores de Mi Tierra | Vevo Playlist", "start_seconds": 271.0, "end_seconds": 281.0}
    ]
  }
}
```

*Trimmed for readability.*

## 🚀 Unlimited Free YouTube Data — Get It in 60 Seconds

1️⃣ Clone and install:
```bash
git clone https://github.com/omkarcloud/youtube-scraper
cd youtube-scraper
python -m pip install -r requirements.txt
```

2️⃣ Start the API:
```bash
python run.py
```

3️⃣ Get your first data:
```bash
curl "http://localhost:8000/videos/details?video=kJQP7kiw5Fk"
```

```json
{
  "video": {
    "id": "kJQP7kiw5Fk",
    "title": "Luis Fonsi - Despacito ft. Daddy Yankee",
    "link": "https://www.youtube.com/watch?v=kJQP7kiw5Fk",
    "duration_seconds": 282,
    "category": "Music",
    "published_at": "2017-01-13T05:00:02Z",
    "view_count": 9132932509,
    "like_count": 56707088,
    "comment_count": 4300000,
    "channel": {"id": "UCxoq-PAQeAdk_zyg8YS0JqA", "name": "Luis Fonsi", "handle": "@LuisFonsiVEVO", "subscriber_count": 32600000, "is_verified": true},
    "music": [
      {"title": "Despacito", "artist": "Luis Fonsi", "album": "Despacito"}
    ],
    "heatmap": [
      {"start_seconds": 0.0, "duration_seconds": 2.8, "intensity": 0.6978}
    ]
  }
}
```

All 28 endpoints are now live at `http://localhost:8000`.

## 📚 Endpoints

28 endpoints cover everything you need.

| Endpoint | Path | Returns |
|---|---|---|
| Video Details | `/videos/details` | Everything about one video: exact views, likes, chapters, heatmap, music |
| Search Suggestions | `/search/suggestions` | YouTube's own autocomplete for any partial query |
| Search | `/search` | Videos, Shorts, channels, playlists and movies with YouTube's filters |
| Video Details (Batch) | `/videos/details/batch` | Views, likes and publish time for 20 videos per call |
| Video / Post Comments | `/videos/comments`, `/posts/comments` | Top or newest comments with likes, replies and pinned flags |
| Comment Replies | `/comments/replies` | Every reply under any comment |
| Video Transcript | `/videos/transcript` | Timed transcript as JSON, text, SRT or WebVTT |
| Video Subtitles | `/videos/subtitles` | Every subtitle track with a ready WebVTT link |
| Related Videos | `/videos/related` | The watch-next column: videos, mixes and playlists |
| Video Streams | `/videos/streams` | Direct video and audio links, HLS for live, storyboards |
| Live Chat | `/videos/live-chat` | Live messages, Super Chats and memberships, or chat replay |
| Channel Details | `/channels/details` | Exact subscribers, views, videos, joined date and links |
| Channel Videos / Shorts / Live | `/channels/videos`, `/channels/shorts`, `/channels/live` | Uploads, Shorts or streams — latest, popular or oldest first |
| Channel Playlists | `/channels/playlists` | Playlists, albums and singles, podcasts or courses |
| Channel Posts | `/channels/posts` | Community posts with images, polls, likes and comments |
| Channel Home | `/channels/home` | Featured video and every shelf on the Home tab |
| Channel Search | `/channels/search` | Search inside one channel's videos and playlists |
| Playlist Details / Videos | `/playlists/details`, `/playlists/videos` | Owner, views, update date; 100 videos per page |
| Post Details | `/posts/details` | One community post with its images or poll |
| Hashtag Videos | `/hashtags/videos` | Videos or Shorts for any hashtag, with usage counts |
| Shorts Sound | `/shorts/sound` | A Short's sound and the Shorts made with it |
| YouTube Charts | `/charts` | Top and trending videos, songs, artists and podcasts by country |
| Explore Feed | `/explore` | News, live, sports, gaming, movies and more, as titled sections |
| Resolve Link | `/resolve` | Any YouTube link turned into its type and ID |

## 🔍 Exploring Parameters

The same API is published on RapidAPI, and its playground is the easiest place to try parameters and see raw responses. Once a request looks right, run it locally for **unlimited free** data.

1. [Subscribe to the free plan](https://rapidapi.com/OmkarCloud/api/best-youtube-scraper-free-1000-calls/pricing) — 1,000 calls/month, no credit card.
2. [Try the endpoints in the playground](https://rapidapi.com/OmkarCloud/api/best-youtube-scraper-free-1000-calls/playground) — every param is pre-filled, so you see real data in one click.
3. Copy the generated code and replace `https://best-youtube-scraper-free-1000-calls.p.rapidapi.com` with `http://localhost:8000`. It will now run against your local API.

```python
import requests

# generated by the playground, host swapped for the local API
response = requests.get(
    "http://localhost:8000/videos/details",
    params={"video": "kJQP7kiw5Fk"},
)
print(response.json())
```

## 💬 Have Questions? We Have Answers.

You're a developer — we know how hard completing a project can be. So we offer full support: just message us and we'll reply ✅ with a solution within 1 working day.

[![Message Us on WhatsApp about YouTube Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/whatsapp-us.png)](https://api.whatsapp.com/send?phone=918178804274&text=I%20need%20help%20using%20the%20YouTube%20Scraper%20API.)

[![Ask Us by Email about YouTube Scraper](https://raw.githubusercontent.com/omkarcloud/assets/master/images/ask-on-email.png)](mailto:happy.to.help@omkar.cloud?subject=Help%20with%20YouTube%20Scraper%20API&body=I%20need%20help%20using%20the%20YouTube%20Scraper%20API.)

## ⚡ Popular Scrapers by Omkar Cloud

- [**Google Maps Scraper (3,100+ GitHub Stars)**](https://github.com/omkarcloud/google-maps-scraper) — type "dentists in New York", get every business as a ready-to-call lead list: phones, emails, websites & reviews. Up to 100K free leads/month.
- [**G2 Scraper**](https://www.omkar.cloud/tools/g2-scraper) — G2 product details, ratings & AI-found contacts
- [**Website Email Contact Scraper**](https://www.omkar.cloud/tools/website-email-contact-scraper) — emails, phones & socials from any website
- [**AliExpress Scraper**](https://www.omkar.cloud/tools/aliexpress-scraper-api) — live product details, SKU variants, stock & shipping
- [**Booking Scraper**](https://www.omkar.cloud/tools/booking-scraper) — Booking.com hotels: prices, ratings, rooms & amenities
- [**IMDb Scraper**](https://github.com/omkarcloud/imdb-scraper) — movies, TV shows, ratings, cast & box office

## ⭐ Love It? [Star It ⭐!](https://github.com/omkarcloud/youtube-scraper)

Star the repo ⭐ and become my star hero!

It's just 1 click, but it means the world to me.

[![Star us on GitHub](https://raw.githubusercontent.com/omkarcloud/google-maps-scraper/master/screenshots/star-us.png)](https://github.com/omkarcloud/youtube-scraper)
