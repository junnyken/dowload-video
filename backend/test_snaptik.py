import httpx
import re

url = "https://unduhtiktok.com/vi/douyin/"
r = httpx.get(url)
html = r.text

print("HTML length:", len(html))
forms = re.findall(r'<form[^>]*>', html)
print("Forms:", forms)
actions = re.findall(r'action="([^"]+)"', html)
print("Actions:", actions)

fetches = re.findall(r'fetch\([\'"]([^\'"]+)[\'"]', html)
print("Fetches:", fetches)
