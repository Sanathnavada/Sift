import re
from dataclasses import dataclass

@dataclass
class Track:
    title: str
    artist: str = ""
    album: str = ""
    image_url: str = ""
    is_dummy: bool = False

    @property
    def sanitized_title(self) -> str:
        # Remove common metadata noise that confuses matching
        clean = re.sub(r"\s*\(From\s+[\"'].+?[\"']\)", "", self.title, flags=re.IGNORECASE)
        clean = re.sub(r"\s*\(feat\..+?\)", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s*[\[\(]Remastered[\]\)]", "", clean, flags=re.IGNORECASE)
        clean = re.sub(r"\s-\sFrom\s.*", "", clean, flags=re.IGNORECASE)
        return clean.strip()

    @property
    def album_acronym(self) -> str:
        if not self.album or len(self.album) < 10: return ""
        words = [w for w in self.album.split() if w.isalnum()]
        if len(words) >= 2: return "".join([w[0] for w in words]).upper()
        return ""

    @property
    def search_query(self) -> str:
        t = self.sanitized_title
        clean_artist = self.artist.split("feat.")[0].strip()
        
        # 1. Dummy Mode (User typed song)
        if self.is_dummy:
            return f"{t} official audio"

        # 2. Short Title Protection (Fix for "Drama", "Amyrai")
        # If title is 1 word or very short, Quote it to force exact match
        if len(t.split()) <= 1 or len(t) < 4:
            return f'"{t}" {clean_artist} official audio'

        # 3. Soundtrack Logic
        if self.album and len(t.split()) < 3:
             return f"{t} {clean_artist} {self.album} audio"
             
        return f"{t} {clean_artist} official audio"

    def __str__(self):
        return f"{self.title} - {self.artist}"
    
@dataclass
class VideoResult:
    id: str
    title: str
    channel: str
    duration: int
    url: str
