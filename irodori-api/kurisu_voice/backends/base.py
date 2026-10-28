from abc import ABC, abstractmethod


class TTSBackend(ABC):
    @abstractmethod
    def health(self):
        raise NotImplementedError

    @abstractmethod
    def synthesize_chunk(self, text, voice_ref="", options=None):
        raise NotImplementedError

    def capabilities(self):
        return {"backend": self.__class__.__name__}

