import unicodedata
import re

def slugify(text: str) -> str:
    """
    Convert text to a URL-friendly and file-friendly slug.
    Also handles Vietnamese characters.
    """
    if not text:
        return "video"
        
    # Normalize unicode to separate characters from diacritical marks
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode('utf-8')
    # Lowercase
    text = text.lower()
    # Remove all non-alphanumeric characters except spaces and hyphens
    text = re.sub(r'[^a-z0-9\s-]', '', text)
    # Replace multiple spaces or hyphens with a single hyphen
    text = re.sub(r'[\s-]+', '-', text).strip('-')
    
    return text or "video"
