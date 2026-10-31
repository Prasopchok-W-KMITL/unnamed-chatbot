from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

Goal = Literal['unknown', 'volume', 'curls', 'straight', 'smooth']


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class ConsultationSlots(Strict):
    goal: Goal = 'unknown'
    history: Literal['unknown', 'none', 'chemical'] = 'unknown'
    hair_length: Literal['unknown', 'short', 'medium', 'long'] = 'unknown'
    risk: Literal['unknown', 'assessment'] = 'unknown'


class Profile(ConsultationSlots):
    declined_services: list[Literal['perm', 'straightening', 'keratin', 'coloring', 'bleach']] = Field(default_factory=list, max_length=5)

    def decision_slots(self):
        return self.model_dump(exclude={'declined_services'})


class Decision(ConsultationSlots):
    intent: Literal['facts', 'recommend', 'clarify', 'unknown']
    fact_ids: list[str] = Field(default_factory=list, max_length=20)
    guidance_ids: list[str] = Field(default_factory=list, max_length=2)


class VisionSummary(Strict):
    kind: Literal['hairstyle', 'unsupported', 'unclear']
    hair_length: Literal['unknown', 'short', 'medium', 'long']
    texture: Literal['unknown', 'straight', 'wavy', 'curly']
    volume: Literal['unknown', 'low', 'high']


class ChatRequest(Strict):
    message: str = Field(min_length=1, max_length=1000)
    session_id: str = Field(default='', max_length=64, pattern=r'^[a-zA-Z0-9_-]*$')


class Answer(Strict):
    request_id: str = ''
    reply: str
    session_id: str = ''
    mode: Literal['demo', 'connected']
    retrieval: Literal['none', 'local', 'lightrag'] = 'none'
    model_used: bool = False
    guard_checked: bool = False
    safety_route: Literal['none', 'local_self_harm', 'guard_self_harm', 'local_distress'] = 'none'
    evidence_ids: list[str] = Field(default_factory=list)
