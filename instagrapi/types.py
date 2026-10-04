"""Only the photo-story types retained from instagrapi."""
from typing import Literal
from pydantic import BaseModel, HttpUrl

StoryResizeMode = Literal["fill", "fit"]

class StoryLink(BaseModel):
    webUri: HttpUrl
    x: float = 0.5126011
    y: float = 0.5168225
    z: float = 0.0
    width: float = 0.50998676
    height: float = 0.25875
    rotation: float = 0.0
