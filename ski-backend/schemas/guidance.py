"""回答完成后的独立交互协议，不参与正文引用或行程卡校验。"""
from typing import Literal
from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class Suggestion(BaseModel):
    id: str
    label: str
    question: str


class Scene(BaseModel):
    resort_id: str
    name: str
    preview_url: HttpUrl | None = None


class LearningResource(BaseModel):
    title: str
    url: HttpUrl
    source: str
    level: str
    topic: str
    updated_at: str
    board: str


class Learning(BaseModel):
    notice: str = ''
    resources: list[LearningResource] = Field(default_factory=list)


class MediaResource(BaseModel):
    resort_id: str
    title: str
    kind: Literal['photo', 'video', 'map']
    url: HttpUrl
    source: str
    published_at: str = '未注明'
    checked_at: str
    verification: Literal['page_checked', 'content_checked']
    rights: Literal['link_only', 'authorized'] = 'link_only'
    notice: str
    category: Literal['cover', 'trail_map', 'terrain', 'facility', 'stay', 'other'] | None = None
    image_url: HttpUrl | None = None
    credit: str | None = None
    author: str | None = None
    board: str | None = None
    level: str | None = None
    topic: str | None = None
    duration: str | None = None
    recommendation: str | None = None

    @field_validator('url', 'image_url', mode='before')
    @classmethod
    def reject_ambiguous_urls(cls, value):
        if value is not None and isinstance(value, str):
            if '\\' in value or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
                raise ValueError('媒体地址不能包含空白、控制字符或反斜线')
        return value

    @field_validator('url', 'image_url')
    @classmethod
    def require_safe_https(cls, value):
        if value is not None and (value.scheme != 'https' or value.username is not None or value.password is not None):
            raise ValueError('媒体地址仅允许无凭据的 HTTPS')
        return value

    @model_validator(mode='after')
    def restrict_display_metadata(self):
        if self.image_url is not None:
            if self.rights != 'authorized' or self.verification != 'content_checked' or self.image_url == self.url:
                self.image_url = None
        if self.verification != 'content_checked' or self.kind != 'video':
            for field in ('author', 'board', 'level', 'topic', 'duration', 'recommendation'):
                setattr(self, field, None)
        return self


class RunFact(BaseModel):
    """一条雪道的档案参数。数字只来自 YAML，不由模型填写。"""
    name: str
    difficulty: str
    length_m: int | None = None
    vertical_m: int | None = None
    slope_deg: float | None = None
    note: str = ''


class RunBrief(BaseModel):
    """聊天里的雪道卡：和模型正文分开，避免把两个口径说成一个总数。"""
    title: str
    source: str
    date: str
    caveat: str = ''
    runs: list[RunFact] = Field(default_factory=list, max_length=6)


class Guidance(BaseModel):
    suggestions: list[Suggestion] = Field(default_factory=list, max_length=3)
    scenes: list[Scene] = Field(default_factory=list)
    learning: Learning = Field(default_factory=Learning)
    media: list[MediaResource] = Field(default_factory=list, max_length=3)
    run_brief: RunBrief | None = None
