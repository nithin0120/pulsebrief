"""Modular source connector base."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.config import SourceConfig, TopicConfig
from app.services.pipeline.article import PipelineArticle


class SourceConnector(ABC):
    def __init__(self, source: SourceConfig) -> None:
        self.source = source

    @abstractmethod
    async def fetch(self, topics: list[TopicConfig]) -> list[PipelineArticle]:
        ...
