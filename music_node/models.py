import re
from dataclasses import dataclass

@dataclass
class Track:
    title: str
    artist: str = ""
    album: str = ""
    image_url: str = ""
    duration_ms: int = 0
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
    def sanitized_album(self) -> str:
        clean = re.sub(
            r"\s*[\(\[]?Original Motion Picture Soundtrack[\)\]]?\s*",
            "",
            self.album,
            flags=re.IGNORECASE,
        )
        clean = re.sub(
            r"\s*[\(\[]?(?:Motion Picture|Original) Soundtrack[\)\]]?\s*",
            "",
            clean,
            flags=re.IGNORECASE,
        )
        return clean.strip(" -()[]")

    @property
    def requires_album_match(self) -> bool:
        return bool(
            re.search(r"\bfrom\b", self.title, flags=re.IGNORECASE)
            or re.search(r"\bsoundtrack\b", self.album, flags=re.IGNORECASE)
        )

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

        parts = [f'"{t}"', clean_artist]
        if self.sanitized_album:
            parts.append(self.sanitized_album)
        parts.append("official audio")
        return " ".join(part for part in parts if part)

    def __str__(self):
        return f"{self.title} - {self.artist}"
    
@dataclass
class VideoResult:
    id: str
    title: str
    channel: str
    duration: int
    url: str
    rank: int = 0
    description: str = ""
