import yt_dlp
import sys

url = "https://www.youtube.com/watch?v=C5yYEAcE_cI"

def test_clients(clients):
    opts = {
        "quiet": True,
        "extractor_args": {
            "youtube": {
                "player_client": clients
            }
        }
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        formats = info.get("formats", [])
        videos = [f for f in formats if f.get("vcodec") != "none"]
        max_res = max([f.get("height", 0) for f in videos]) if videos else 0
        print(f"Clients {clients}: Found {len(formats)} formats, Max Res: {max_res}p")

test_clients(["web", "mweb", "android", "ios"])
test_clients(["android", "ios"])
test_clients(["tv", "web"])
test_clients(["ios"])
