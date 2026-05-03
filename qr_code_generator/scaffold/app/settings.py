"""In-memory system settings exposing the Q1-Q5 design choices as live toggles."""
from dataclasses import dataclass, field, asdict
from typing import Literal


TokenStrategy = Literal["random", "hash_only", "hash_with_nonce"]
NormalizationMode = Literal["conservative", "aggressive"]
RedirectStatus = Literal[301, 302]
GoneStatus = Literal[404, 410]


@dataclass
class SystemSettings:
    token_length: int = 7
    token_strategy: TokenStrategy = "hash_with_nonce"
    normalization_mode: NormalizationMode = "aggressive"
    redirect_status: RedirectStatus = 302
    gone_status: GoneStatus = 410

    def to_dict(self) -> dict:
        return asdict(self)

    def update(self, **kwargs) -> None:
        valid_strategies = ("random", "hash_only", "hash_with_nonce")
        valid_modes = ("conservative", "aggressive")

        if "token_length" in kwargs:
            v = int(kwargs["token_length"])
            if not 4 <= v <= 12:
                raise ValueError("token_length must be 4-12")
            self.token_length = v
        if "token_strategy" in kwargs:
            v = kwargs["token_strategy"]
            if v not in valid_strategies:
                raise ValueError(f"token_strategy must be one of {valid_strategies}")
            self.token_strategy = v
        if "normalization_mode" in kwargs:
            v = kwargs["normalization_mode"]
            if v not in valid_modes:
                raise ValueError(f"normalization_mode must be one of {valid_modes}")
            self.normalization_mode = v
        if "redirect_status" in kwargs:
            v = int(kwargs["redirect_status"])
            if v not in (301, 302):
                raise ValueError("redirect_status must be 301 or 302")
            self.redirect_status = v
        if "gone_status" in kwargs:
            v = int(kwargs["gone_status"])
            if v not in (404, 410):
                raise ValueError("gone_status must be 404 or 410")
            self.gone_status = v


settings = SystemSettings()
