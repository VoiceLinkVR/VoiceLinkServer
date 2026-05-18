from pydantic import BaseModel, Field


class VoiceClonePresetMarketItem(BaseModel):
    id: str
    name: str
    description: str = ""
    version: str = ""
    languages: list[str] = Field(default_factory=list)
    providers: list[str] = Field(default_factory=list)
    fileSize: int = 0
    sha256: str = ""
    updatedAt: str = ""
    createdAt: str = ""
    enabled: bool = True


class VoiceClonePresetMarketListResponse(BaseModel):
    items: list[VoiceClonePresetMarketItem] = Field(default_factory=list)


class VoiceClonePresetMarketUpdateRequest(BaseModel):
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    version: str | None = Field(default=None, max_length=64)
    languages: list[str] | str | None = None
    providers: list[str] | str | None = None
    enabled: bool | None = None
